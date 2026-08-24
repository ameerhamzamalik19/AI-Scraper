from typing import Any, Dict, List
from openai import OpenAI
from database_sync import execute_query
from workers.embedder_worker import get_embedding
import os
import logging
import json
from openrouter import OpenRouter

logger = logging.getLogger(__name__)

# ============================================
# PROMPT TEMPLATES - NATURAL & CONVERSATIONAL
# ============================================

SYSTEM_PROMPT = """You are a helpful, knowledgeable assistant that answers questions based on provided website content.

## YOUR JOB:
- Answer naturally and conversationally, like a human expert
- Synthesize information from the context into clear, flowing prose
- NEVER mention "based on the context," "according to the provided content," or "the chunks say"
- NEVER show internal reasoning or thinking
- NEVER cite sources or mention chunks
- If the context doesn't contain enough information, say: "I don't have enough information about that in the available content."
- Be concise and direct - don't over-explain

## RULES:
1. Use ONLY information from the context - no external knowledge
2. Write naturally - like you already know this information
3. Synthesize across multiple chunks when relevant
4. If information is incomplete, acknowledge it simply and move on
5. No source citations, no "chunk numbers," no "according to the text"
6. Be confident in your answer - don't hedge or qualify unnecessarily

## EXAMPLE:
Bad: "According to Chunk 4, NVIDIA does graphics. Chunk 2 says they do AI."
Good: "NVIDIA specializes in graphics, rendering, simulation, and AI technologies."

Remember: Write like a helpful expert, not a research paper."""

def build_user_prompt(question: str, context_chunks: List[Dict[str, Any]]) -> str:
    """Build user prompt with structured context - sources hidden from final output."""
    
    # Extract just the content - no chunk markers, no formatting for the AI
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

Your answer:"""


def create_standalone_question(
    user_question: str,
    chat_history: List[Dict[str, Any]] = None
) -> str:
    """Rewrite a follow-up question so it can be used for independent retrieval."""
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

Rewrite the latest user question as a standalone question. Resolve references
such as "it", "they", or "that" using the conversation history. Preserve the
user's meaning and return only the rewritten question, with no explanation."""

    try:
        with OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY")) as client:
            response = client.chat.send(
                model="nvidia/nemotron-3-ultra-550b-a55b:free",
                messages=[
                    {
                        "role": "system",
                        "content": "Rewrite follow-up questions into standalone questions. Return only the question."
                    },
                    {"role": "user", "content": prompt}
                ],
            )

            standalone_question = response.choices[0].message.content.strip()
            print(f"✅ Standalone question generated: {standalone_question}")
            return standalone_question or user_question
    except Exception as e:
        logger.warning("Standalone question generation failed: %s", e)
        return user_question

# ============================================
# DATABASE FUNCTIONS
# ============================================

def retrieve_relevant_chunks(
    embedding: List[float],
    chat_id: str,
    limit: int = 5,
    min_similarity: float = 0.0
) -> List[Dict[str, Any]]:
    """
    Return completed chunks nearest to an embedding using cosine distance.
    Compatible with halfvec(2048) embeddings in your schema.
    """
    if not embedding or not chat_id:
        print("❌ No embedding provided")
        return []

    if limit < 1:
        raise ValueError("limit must be greater than zero")

    vector = "[{}]".format(",".join(str(value) for value in embedding))
    
    # Try with halfvec casting (your schema)
    try:
        chunks = execute_query(
            """
                 SELECT chunk_id, page_version_id, document_id, chunk_index,
                     chunk_type, content, context_prefix, heading_path,
                     token_count, similarity
                 FROM (
                  SELECT DISTINCT ON (LOWER(REGEXP_REPLACE(c.content, '\\s+', ' ', 'g')))
                      c.id AS chunk_id,
                      c.page_version_id,
                      c.document_id,
                      c.chunk_index,
                      c.chunk_type,
                      c.content,
                      c.context_prefix,
                      c.heading_path,
                      c.token_count,
                      1 - (c.embedding <=> %s::halfvec) AS similarity
                  FROM chunks c
                  JOIN page_versions pv ON pv.id = c.page_version_id
                  JOIN pages p ON p.id = pv.page_id
                  WHERE c.embedding_status = 'COMPLETED'
                    AND c.embedding IS NOT NULL
                    AND p.chat_id = %s
                  ORDER BY LOWER(REGEXP_REPLACE(c.content, '\\s+', ' ', 'g')),
                        c.embedding <=> %s::halfvec
                 ) AS unique_chunks
                 ORDER BY similarity DESC
                 LIMIT %s
                 """,
    (vector, chat_id, vector, limit),
        )

        if not chunks:
            print("❌ No chunks found for the given embedding")
            return ["Nothing found for the given embedding."]

        print(f"✅ Retrieved {len(chunks)} relevant chunks from the database")
        print(f"Sample chunk: {json.dumps(chunks, indent=2)}")
        
        # Filter by minimum similarity threshold if set
        if min_similarity > 0:
            chunks = [chunk for chunk in chunks if chunk.get('similarity', 0) >= min_similarity]
        
        return chunks
        
    except Exception as e:
        print(f"❌ Error in retrieve_relevant_chunks: {e}")
        return ["An error occurred while retrieving relevant chunks."]

# ============================================
# GENERATION FUNCTIONS
# ============================================

def generate_response(
    user_question: str, 
    context_chunks: List[Dict[str, Any]],
    chat_history: List[Dict[str, Any]] = None
) -> str:
    """
    Generate a natural, conversational response based on the provided context.
    Single LLM call - simple, fast, reliable.
    """
    # user_prompt = build_user_prompt(user_question, context_chunks)

    # history_messages = [
    #     {
    #         "role": message["role"],
    #         "content": message["content"]
    #     }
    #     for message in (chat_history or [])
    #     if message.get("role") in {"user", "assistant"}
    #     and message.get("content")
    # ]

    # try:
    #     with OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY")) as client:
    #         response = client.chat.send(
    #             model="nvidia/nemotron-3-ultra-550b-a55b:free",
    #             messages=[
    #                 {"role": "system", "content": SYSTEM_PROMPT},
    #                 *history_messages,
    #                 {"role": "user", "content": user_prompt}
    #             ],
    #         )

    #         answer = response.choices[0].message.content
    #         return answer

    # except Exception as e:
    #     print(f"❌ LLM API call failed: {e}")
    #     import traceback
    #     traceback.print_exc()
    #     return f"I encountered an error: {str(e)}"
    
    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=os.getenv("GROQ_API_KEY")
    )
    
    user_prompt = build_user_prompt(user_question, context_chunks)

    try:
        completion = client.chat.completions.create(
            model="groq/compound-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,  # Slightly higher for more natural language
            top_p=0.85,
            max_tokens=500,  # Shorter, more concise responses
            # extra_body={"chat_template_kwargs": {"thinking": True, "reasoning_effort": "high"}},
            stream=False
        )
        
        answer = completion.choices[0].message.content
        return answer

    except Exception as e:
        print(f"❌ LLM API call failed: {e}")
        import traceback
        traceback.print_exc()
        return f"I encountered an error: {str(e)}"

def answer_user_question(
    user_question: str,
    chat_id: str,
    project_id: str = None,
    page_id: str = None,
    chat_history: List[Dict[str, Any]] = None
) -> str:
    """
    Complete RAG pipeline: embed question, retrieve relevant chunks, generate answer.
    Returns natural, conversational answers without sources or reasoning.
    """
    if not user_question or not user_question.strip():
        return "Please provide a valid question."

    try:
        # Step 1: Rewrite follow-up questions for context-independent retrieval
        standalone_question = create_standalone_question(
            user_question,
            chat_history
        )

        # Step 2: Get embedding for the standalone question
        embedding = get_embedding(standalone_question)
        if not embedding:
            return "I couldn't process that question. Please try again."

        # Step 3: Retrieve relevant chunks
        chunks = retrieve_relevant_chunks(embedding, chat_id=chat_id, limit=10)
        
        if not chunks:
            return "I don't have enough information about that in the available content."

        # Step 4: Generate natural response with the original history
        response = generate_response(standalone_question, chunks, chat_history)
        print("✅ Generated response:", response)
        # Step 4: Return just the answer - clean, natural, no extra fluff
        return response

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Error: {str(e)}"

# ============================================
# UTILITY FUNCTIONS
# ============================================

def check_embedding_dimension() -> Dict[str, Any]:
    """Check the dimension of embeddings in the database."""
    result = execute_query(
        """
        SELECT 
            embedding::text as embedding_sample
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
