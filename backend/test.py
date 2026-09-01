import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

# Initialize client (Ensure you set your GROQ_API_KEY environment variable)
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

context = "Product Alpha will launch on October 12th. Product Beta is delayed until Q1 2027."
question = "When is Product Alpha launching?"

completion = client.chat.completions.create(
    model="llama-3.1-8b-instant",
    messages=[
        {"role": "system", "content": "You are a precise RAG assistant. Synthesize a clear answer using ONLY the provided context."},
        {"role": "user", "content": f"Context: {context}\n\nQuestion: {question}"}
    ],
    temperature=0.0, # Kept at 0 for strict, deterministic RAG synthesis
)

print(completion.choices[0].message.content)
