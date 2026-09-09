# api/routes/retrieval_pipeline.py
from typing import Any, Dict, List, Optional, Tuple
from ollama import Client
from database_sync import execute_query
from utils.embedding_service import get_embedding
import os
import logging
import json
import re
from collections import defaultdict
import math

logger = logging.getLogger(__name__)


# ============================================
# CONFIGURATION
# ============================================

CATEGORY_BOOST = {
    'main_content': 1.0,
    'header_nav': 0.8,
    'footer': 0.7,
    'sidebar': 0.5,
    'excluded': 0.0,
    'ui_summary': 0.6,
}

ENTITY_BOOST = {
    'product': 1.3,
    'article_body': 1.2,
    'faq': 1.4,
    'code_example': 1.1,
    'table': 1.1,
    'card': 1.0,
    'image': 1.05,
    'content': 1.0,
    'navigation': 0.5,
    'footer': 0.4,
}

EXCLUDED_CATEGORIES = ['excluded', 'filter', 'modal', 'cookie']

# Retrieval confidence thresholds
MIN_SIMILARITY_THRESHOLD = 0.1  # Minimum acceptable similarity
MIN_RRF_SCORE_THRESHOLD = 0.02   # Minimum RRF score
MIN_SCORE_GAP = 0.05             # Minimum gap between top and second scores


def format_generation_error(error: Exception) -> str:
    error_text = str(error)
    retry_match = re.search(r"try again in ([^.]+)", error_text, re.IGNORECASE)

    if "429" in error_text or "rate_limit" in error_text.lower() or "rate limit" in error_text.lower():
        retry_text = f" Please try again in {retry_match.group(1)}." if retry_match else " Please try again shortly."
        return f"The AI service is temporarily rate-limited.{retry_text}"

    logger.exception("LLM generation failed")
    return "I couldn't generate an answer right now. Please try again later."


SYSTEM_PROMPT = """You are a helpful assistant for a specific website. You have access to content scraped from that site.

## HOW TO RESPOND:

**For greetings and small talk** (hi, how are you, thanks, etc.):
- Respond naturally and briefly like a human would
- Don't mention the website or context
- Keep it to 1-2 sentences max

**For questions clearly about the website content:**
- Answer using ONLY the provided context
- Synthesize naturally — never say "based on the context" or "the chunks say"
- If the context doesn't answer it, say: "I don't have that information available."

**For questions that mix general knowledge + website content:**
- Use the context as your primary source
- You may fill in basic, universally-known facts (definitions, common concepts) to make the answer flow naturally
- Never speculate or fabricate specific details, numbers, or claims not in the context

## RULES:
1. Never mention "chunks," "context," "scraped content," or internal workings
2. Never cite sources or reference numbers
3. Be concise and direct
4. For factual questions about the site's topic, stick to the context
5. When genuinely uncertain, say so simply — don't over-hedge
6. Write like a knowledgeable human, not a research paper"""


def build_user_prompt(question: str, context_chunks: List[Dict[str, Any]]) -> str:
    context_parts = []
    for chunk in context_chunks:
        content = chunk.get('content', '').strip()
        if content:
            context_parts.append(content)
    
    context_text = "\n\n---\n\n".join(context_parts)
    
    return f"""Here is the information you have access to:

{context_text}

The user asked: {question}

Write a natural, conversational answer using ONLY this information.
- Synthesize the information into flowing prose
- Don't mention that you're using context or sources
- Write like you already know this
- Be direct and helpful
- If you don't have enough information, simply say: "I don't have enough information about that in the available content."
- If the information is only weakly related, say: "I don't have enough specific information about that."

Your answer:"""


def create_standalone_question(
    user_question: str,
    chat_history: List[Dict[str, Any]] = None
) -> str:
    """Rewrite follow-up questions preserving all distinctive entities."""
    history_messages = [
        f"{message['role']}: {message['content']}"
        for message in (chat_history or [])
        if message.get("role") in {"user", "assistant"}
        and message.get("content")
    ]

    if not history_messages:
        return user_question

    history_text = "\n".join(history_messages)
    
    prompt = f"""Conversation history:
{history_text}

Latest user question: {user_question}

Rewrite the latest user question as a standalone question. 
CRITICAL RULES:
1. Preserve ALL distinctive nouns, names, and entities (e.g., "iolfree", "troido", "Nunjucks", "Jinja2", "System 6")
2. Do NOT remove or generalize any proper nouns
3. Keep technical terms intact
4. Resolve references like "it", "they", "that" using the conversation history
5. Return only the rewritten question, with no explanation

Rewritten question:"""

    try:
        client = Client(
            host="https://ollama.com",
            headers={'Authorization': 'Bearer ' + os.getenv("OLLAMA_API_KEY")}
        )
        
        response = client.chat(
            model="gpt-oss:20b",
            messages=[
                {
                    "role": "system", 
                    "content": "Rewrite follow-up questions into standalone questions preserving all entities and proper nouns. Return only the question."
                },
                {"role": "user", "content": prompt}
            ],
            stream=False
        )

        standalone_question = response['message']['content'].strip()
        
        # Log both for debugging
        print(f"📝 ORIGINAL: {user_question}")
        print(f"📝 REWRITTEN: {standalone_question}")
        
        return standalone_question or user_question
    except Exception as e:
        logger.warning("Standalone question generation failed: %s", e)
        return user_question


# ============================================================
# PRIORITY 1: HYBRID RETRIEVAL WITH RRF
# ============================================================

def retrieve_relevant_chunks_hybrid(
    query_text: str,
    query_embedding: List[float],
    chat_id: str,
    limit: int = 10,
    rrf_k: int = 60,
    min_similarity: float = MIN_SIMILARITY_THRESHOLD,
    min_rrf_score: float = MIN_RRF_SCORE_THRESHOLD,
    min_score_gap: float = MIN_SCORE_GAP,
    entity_filter: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Hybrid retrieval combining vector similarity and keyword search using RRF.
    """
    if not query_text or not query_embedding:
        return [], {'error': 'No query or embedding provided'}
    
    vector = "[{}]".format(",".join(str(value) for value in query_embedding))
    
    # ✅ Build entity filter clause safely (no placeholder injection)
    entity_clause = ""
    entity_params = []
    if entity_filter:
        # Use LIKE with parameterized value
        entity_clause = """
            AND EXISTS (
                SELECT 1 FROM documents d 
                WHERE d.id = c.document_id 
                AND (d.metadata->>'url' ILIKE %s 
                     OR d.metadata->>'page_title' ILIKE %s)
            )
        """
        entity_params = [f"%{entity_filter}%", f"%{entity_filter}%"]
    
    try:
        # ✅ Vector search - adjust params based on entity_filter
        vector_query = f"""
            SELECT 
                c.id AS chunk_id,
                c.content,
                c.chunk_category,
                c.entity_type,
                c.section_title,
                c.information_density,
                c.heading_path,
                d.id as document_id,
                d.metadata->>'url' as source_url,
                1 - (c.embedding <=> %s::halfvec) AS similarity
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            JOIN page_versions pv ON pv.id = c.page_version_id
            JOIN pages p ON p.id = pv.page_id
            WHERE c.embedding_status = 'COMPLETED'
                AND c.embedding IS NOT NULL
                AND p.chat_id = %s
                AND c.chunk_category NOT IN ('excluded', 'filter', 'modal', 'cookie')
                AND COALESCE(c.information_density, 1.0) >= 0.01
                {entity_clause}
            ORDER BY c.embedding <=> %s::halfvec
            LIMIT %s
        """
        
        # Build parameters
        vector_params = [vector, chat_id] + entity_params + [vector, limit * 3]
        vector_results = execute_query(vector_query, tuple(vector_params))
        
        # ✅ Keyword search - adjust params based on entity_filter
        keyword_query = f"""
            SELECT 
                c.id AS chunk_id,
                c.content,
                c.chunk_category,
                c.entity_type,
                c.section_title,
                c.information_density,
                c.heading_path,
                d.id as document_id,
                d.metadata->>'url' as source_url,
                ts_rank(c.content_tsv, plainto_tsquery('english', %s)) AS similarity
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            JOIN page_versions pv ON pv.id = c.page_version_id
            JOIN pages p ON p.id = pv.page_id
            WHERE c.embedding_status = 'COMPLETED'
                AND c.content_tsv IS NOT NULL
                AND c.content_tsv @@ plainto_tsquery('english', %s)
                AND p.chat_id = %s
                AND c.chunk_category NOT IN ('excluded', 'filter', 'modal', 'cookie')
                AND COALESCE(c.information_density, 1.0) >= 0.01
                {entity_clause}
            ORDER BY similarity DESC
            LIMIT %s
        """
        
        # Build parameters for keyword search
        keyword_params = [query_text, query_text, chat_id] + entity_params + [limit * 3]
        keyword_results = execute_query(keyword_query, tuple(keyword_params))
        
        print(f"🔍 Vector results: {len(vector_results)}, Keyword results: {len(keyword_results)}")
        
        # Track if we have lexical matches
        has_lexical_match = len(keyword_results) > 0
        
        # If no results at all
        if not vector_results and not keyword_results:
            return [], {
                'has_results': False,
                'has_lexical_match': False,
                'confidence': 0.0,
                'reason': 'No results from either search method'
            }
        
        # If only vector results, use them but note low confidence
        if vector_results and not keyword_results:
            print("⚠️ Only vector results available - no lexical match")
            for chunk in vector_results:
                category = chunk.get('chunk_category', 'main_content')
                entity_type = chunk.get('entity_type', 'content')
                category_boost = CATEGORY_BOOST.get(category, 0.5)
                entity_boost = ENTITY_BOOST.get(entity_type, 1.0)
                chunk['category_boost'] = category_boost
                chunk['entity_boost'] = entity_boost
                chunk['adjusted_similarity'] = chunk.get('similarity', 0) * category_boost * entity_boost
            
            vector_results.sort(key=lambda x: x.get('adjusted_similarity', 0), reverse=True)
            
            # Check if top score is high enough
            top_score = vector_results[0].get('adjusted_similarity', 0) if vector_results else 0
            
            if top_score < min_similarity:
                print(f"⚠️ Low confidence: top similarity {top_score:.4f} < {min_similarity}")
                return [], {
                    'has_results': True,
                    'has_lexical_match': False,
                    'confidence': top_score,
                    'reason': f'Low similarity ({top_score:.4f} < {min_similarity})'
                }
            
            return vector_results[:limit], {
                'has_results': True,
                'has_lexical_match': False,
                'confidence': top_score,
                'reason': 'Vector search only'
            }
        
        # If only keyword results, use them
        if keyword_results and not vector_results:
            print("📝 Only keyword results available")
            for chunk in keyword_results:
                category = chunk.get('chunk_category', 'main_content')
                entity_type = chunk.get('entity_type', 'content')
                category_boost = CATEGORY_BOOST.get(category, 0.5)
                entity_boost = ENTITY_BOOST.get(entity_type, 1.0)
                chunk['category_boost'] = category_boost
                chunk['entity_boost'] = entity_boost
                chunk['adjusted_similarity'] = chunk.get('similarity', 0) * category_boost * entity_boost
            
            keyword_results.sort(key=lambda x: x.get('adjusted_similarity', 0), reverse=True)
            return keyword_results[:limit], {
                'has_results': True,
                'has_lexical_match': True,
                'confidence': 0.8,
                'reason': 'Keyword search only'
            }
        
        # RRF Fusion - when we have both
        scores = defaultdict(float)
        chunk_data = {}
        
        # Score vector results
        for rank, result in enumerate(vector_results):
            chunk_id = result['chunk_id']
            scores[chunk_id] += 1 / (rrf_k + rank + 1)
            chunk_data[chunk_id] = result
            chunk_data[chunk_id]['vector_rank'] = rank + 1
            chunk_data[chunk_id]['similarity'] = result.get('similarity', 0)
        
        # Score keyword results
        for rank, result in enumerate(keyword_results):
            chunk_id = result['chunk_id']
            scores[chunk_id] += 1 / (rrf_k + rank + 1)
            if chunk_id not in chunk_data:
                chunk_data[chunk_id] = result
            chunk_data[chunk_id]['keyword_rank'] = rank + 1
            if result.get('similarity', 0) > chunk_data[chunk_id].get('similarity', 0):
                chunk_data[chunk_id]['similarity'] = result.get('similarity', 0)
        
        # Apply boosts
        for chunk in chunk_data.values():
            category = chunk.get('chunk_category', 'main_content')
            entity_type = chunk.get('entity_type', 'content')
            category_boost = CATEGORY_BOOST.get(category, 0.5)
            entity_boost = ENTITY_BOOST.get(entity_type, 1.0)
            chunk['category_boost'] = category_boost
            chunk['entity_boost'] = entity_boost
            chunk['rrf_score'] = scores[chunk['chunk_id']]
            chunk['adjusted_similarity'] = scores[chunk['chunk_id']] * category_boost * entity_boost
        
        sorted_chunks = sorted(chunk_data.values(), key=lambda x: x['adjusted_similarity'], reverse=True)
        
        # Confidence check
        top_score = sorted_chunks[0]['adjusted_similarity'] if sorted_chunks else 0
        second_score = sorted_chunks[1]['adjusted_similarity'] if len(sorted_chunks) > 1 else 0
        score_gap = top_score - second_score
        
        print(f"📊 Top RRF score: {top_score:.4f}, Score gap: {score_gap:.4f}, Lexical match: {has_lexical_match}")
        
        # Determine confidence
        confidence_reason = []
        confidence_score = 0.0
        
        if has_lexical_match:
            confidence_score += 0.4
            confidence_reason.append("lexical match")
        
        if top_score > 0.05:
            confidence_score += 0.3
            confidence_reason.append(f"RRF score {top_score:.3f}")
        
        if score_gap > min_score_gap:
            confidence_score += 0.2
            confidence_reason.append(f"score gap {score_gap:.3f}")
        
        # Apply threshold check
        if top_score < min_rrf_score:
            print(f"⚠️ Low confidence: RRF score {top_score:.4f} < {min_rrf_score}")
            return [], {
                'has_results': True,
                'has_lexical_match': has_lexical_match,
                'confidence': top_score,
                'reason': f'Low RRF score ({top_score:.4f} < {min_rrf_score})'
            }
        
        print(f"✅ Confidence: {confidence_score:.2f} ({', '.join(confidence_reason)})")
        
        return sorted_chunks[:limit], {
            'has_results': True,
            'has_lexical_match': has_lexical_match,
            'confidence': confidence_score,
            'rrf_score': top_score,
            'score_gap': score_gap,
            'reason': ', '.join(confidence_reason)
        }
        
    except Exception as e:
        logger.exception("Error in hybrid retrieval: %s", e)
        return [], {'error': str(e)}


# ============================================================
# PRIORITY 4: RETRIEVAL CONFIDENCE GATE
# ============================================================

def should_generate_answer(
    chunks: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    min_confidence: float = 0.1
) -> Tuple[bool, str]:
    """
    Determine if we should generate an answer based on retrieval confidence.
    Returns: (should_generate, reason)
    """
    if not chunks:
        return False, "No chunks retrieved"
    
    confidence = metadata.get('confidence', 0.0)
    has_lexical_match = metadata.get('has_lexical_match', False)
    
    # Check if we have a lexical match
    if has_lexical_match:
        # Lexical match is strong evidence - we can generate with lower confidence
        if confidence > min_confidence:
            return True, f"Lexical match with confidence {confidence:.2f}"
        else:
            return False, f"Lexical match but low confidence {confidence:.2f}"
    
    # No lexical match - need higher confidence
    if confidence < min_confidence:
        return False, f"No lexical match, confidence {confidence:.2f} < {min_confidence}"
    
    # Check top score
    top_score = chunks[0].get('adjusted_similarity', 0)
    if top_score < 0.1:
        return False, f"Top score too low: {top_score:.3f}"
    
    return True, f"Sufficient confidence: {confidence:.2f}, top score: {top_score:.3f}"


# ============================================================
# GENERATION FUNCTIONS
# ============================================================

def generate_response(
    user_question: str, 
    context_chunks: List[Dict[str, Any]],
    chat_history: List[Dict[str, Any]] = None
) -> str:
    """Generate a natural, conversational response."""
    user_prompt = build_user_prompt(user_question, context_chunks)

    history_messages = [
        {
            "role": message["role"],
            "content": message["content"]
        }
        for message in (chat_history or [])
        if message.get("role") in {"user", "assistant"}
        and message.get("content")
    ]

    try:
        client = Client(
            host="https://ollama.com",
            headers={'Authorization': 'Bearer ' + os.getenv("OLLAMA_API_KEY")}
        )
        
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history_messages,
            {"role": "user", "content": user_prompt}
        ]
        
        response = client.chat(
            model="gpt-oss:20b",
            messages=messages,
            stream=False,
            options={
                "temperature": 0.2,
                "top_p": 0.85,
                "num_predict": 500,
            }
        )
        
        answer = response['message']['content']
        return answer

    except Exception as e:
        return format_generation_error(e)


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def answer_user_question(
    user_question: str,
    chat_id: str,
    project_id: str = None,
    page_id: str = None,
    chat_history: List[Dict[str, Any]] = None,
    attempt: int = 1,
    use_hybrid_search: bool = True,
    entity_filter: Optional[str] = None
) -> str:
    """
    Complete RAG pipeline with hybrid retrieval and confidence gating.
    """
    if not user_question or not user_question.strip():
        return "Please provide a valid question."

    if attempt > 3:
        return "I'm having trouble processing your question. Please try again later."

    try:
        # Step 1: Rewrite question preserving entities
        standalone_question = create_standalone_question(
            user_question,
            chat_history
        )

        # Step 2: Get embedding
        embedding = get_embedding(standalone_question)
        if not embedding:
            return "I couldn't process that question. Please try again."

        # Step 3: Extract entity for filtering
        # This is a simple implementation - in production you'd want a proper entity extractor
        import re
        entity_patterns = [
            r'~([a-zA-Z0-9_]+)',  # ~username pattern
            r'([A-Z][a-z]+[A-Z][a-z]+)',  # CamelCase patterns like "Nunjucks"
        ]
        for pattern in entity_patterns:
            matches = re.findall(pattern, standalone_question)
            if matches:
                # Use the first detected entity as a document filter.
                entity_filter = matches[0]
                print(f"🎯 Detected entity: {entity_filter}")
                break

        # Step 4: Retrieve with hybrid search
        chunks, metadata = retrieve_relevant_chunks_hybrid(
            standalone_question,
            embedding,
            chat_id=chat_id,
            limit=10,
            entity_filter=entity_filter,
            min_similarity=MIN_SIMILARITY_THRESHOLD
        )

        # Step 5: Confidence gate
        should_generate, reason = should_generate_answer(chunks, metadata)
        
        if not should_generate:
            print(f"🚫 Generation blocked: {reason}")
            return "I don't have enough information about that in the available content."

        if not chunks:
            return "I don't have enough information about that in the available content."

        # Log what we're using
        print(f"📊 Using {len(chunks)} chunks for generation:")
        for i, chunk in enumerate(chunks[:3]):
            entity = chunk.get('entity_type', 'unknown')
            category = chunk.get('chunk_category', 'unknown')
            similarity = chunk.get('adjusted_similarity', chunk.get('similarity', 0))
            source = chunk.get('source_url', 'unknown')[:50]
            content_preview = chunk.get('content', '')[:80].replace('\n', ' ')
            print(f"  #{i+1}: [{entity}] [{category}] sim:{similarity:.4f} - {content_preview}...")
            print(f"      Source: {source}")

        # Step 6: Generate response using the user's original wording. The
        # standalone question is only a retrieval representation.
        response = generate_response(user_question, chunks, chat_history)
        print("✅ Generated response:", response)
        
        if not response or not response.strip():
            response = generate_response(user_question, chunks, chat_history)
            if not response or not response.strip():
                return "I couldn't generate an answer right now. Please try again later."
        return response

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Error: {str(e)}"


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def check_embedding_dimension() -> Dict[str, Any]:
    result = execute_query(
        """
        SELECT embedding::text as embedding_sample
        FROM chunks 
        WHERE embedding IS NOT NULL 
        LIMIT 1
        """
    )
    if result and result[0].get('embedding_sample'):
        sample = result[0]['embedding_sample']
        dims = sample.count(',') + 1
        return {'dimension': dims}
    return {'dimension': 0}


def get_available_categories(chat_id: str) -> Dict[str, int]:
    result = execute_query(
        """
        SELECT c.chunk_category, COUNT(*) as count
        FROM chunks c
        JOIN page_versions pv ON pv.id = c.page_version_id
        JOIN pages p ON p.id = pv.page_id
        WHERE c.embedding_status = 'COMPLETED'
          AND c.embedding IS NOT NULL
          AND p.chat_id = %s
        GROUP BY c.chunk_category
        """,
        (chat_id,)
    )
    return {row['chunk_category']: row['count'] for row in result} if result else {}


def get_chunk_stats(chat_id: str) -> Dict[str, Any]:
    result = execute_query(
        """
        SELECT 
            COUNT(*) as total_chunks,
            AVG(c.information_density) as avg_info_density,
            SUM(CASE WHEN c.entity_type = 'product' THEN 1 ELSE 0 END) as product_count,
            SUM(CASE WHEN c.entity_type = 'faq' THEN 1 ELSE 0 END) as faq_count
        FROM chunks c
        JOIN page_versions pv ON pv.id = c.page_version_id
        JOIN pages p ON p.id = pv.page_id
        WHERE c.embedding_status = 'COMPLETED'
          AND c.embedding IS NOT NULL
          AND p.chat_id = %s
        """,
        (chat_id,)
    )
    return dict(result[0]) if result else {}