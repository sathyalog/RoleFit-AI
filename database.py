import os
import streamlit as st
from dotenv import load_dotenv
from pinecone import Pinecone, ServerlessSpec

load_dotenv(override=True)

index_name = "resume-analyser"

@st.cache_resource
def get_pinecone_index():
    """Initializes Pinecone once and caches the index object across Streamlit rerenders.
    Returns None when PINECONE_API_KEY is missing or Pinecone is unreachable."""
    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        print("PINECONE_API_KEY not set; skipping Pinecone.")
        return None
    try:
        return _connect_index(api_key)
    except Exception as e:
        print(f"Pinecone unavailable: {e}")
        return None


def _connect_index(api_key: str):
    pc = Pinecone(api_key=api_key)

    if not pc.has_index(index_name):
        print(f"Creating index {index_name}..")
        pc.create_index(
            name=index_name,
            dimension=384,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        print(f"Index {index_name} is created :)")
    else:
        print(f"Index {index_name} already exists")

    # Return the connected index object directly
    return pc.Index(index_name)
