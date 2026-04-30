from langchain_groq import ChatGroq
import os
from dotenv import load_dotenv
load_dotenv()


llm = ChatGroq(
    model="llama-3.1-8b-instant",
    api_key=os.getenv("GROQ_API_KEY"),
)

request = llm.invoke("what is the capital of maharashtra?")
print(request.content)

