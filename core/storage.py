import os
import json
from typing import Dict, Any

import streamlit as st

from core.app_mode import is_public_mode

DATA_DIR = "data"

FILES = {
    "skills": os.path.join(DATA_DIR, "skills.json"),
    "challenges": os.path.join(DATA_DIR, "challenges.json"),
    "projects": os.path.join(DATA_DIR, "projects.json"),
    "misc": os.path.join(DATA_DIR, "misc.json"),
    "responsibilities": os.path.join(DATA_DIR, "responsibilities.json")
}


def _session_store() -> Dict[str, list]:
    """Per-visitor, in-memory profile store used in public mode (never touches disk)."""
    if "profile_store" not in st.session_state:
        st.session_state.profile_store = {key: [] for key in FILES}
    return st.session_state.profile_store


def load_json_data(file_key: str) -> list:
    if is_public_mode():
        return list(_session_store()[file_key])

    filepath = FILES.get(file_key)
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []


def save_json_data(file_key: str, new_entry: Dict[str, Any]):
    if is_public_mode():
        _session_store()[file_key].append(new_entry)
        return

    filepath = FILES.get(file_key)
    existing_data = load_json_data(file_key)
    existing_data.append(new_entry)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(existing_data, f, indent=4)


def reset_json_data(file_key: str):
    if is_public_mode():
        _session_store()[file_key] = []
        return

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(FILES[file_key], "w", encoding="utf-8") as f:
        json.dump([], f)


def sync_to_vector_db(index_obj, doc_id: str, text_content: str, metadata: dict):
    """Placeholder vector update: generates mock sparse/dense vectors or upserts text chunks into Pinecone."""
    if index_obj:
        # Pass text to your embedding model (e.g. OpenAI/Cohere/HuggingFace)
        # index_obj.upsert(vectors=[(doc_id, vector_embedding, metadata)])
        pass
