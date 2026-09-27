import importlib.util

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine

# Prefer the large spaCy model when installed locally; the hosted deployment
# ships only the small one (pinned in pyproject) to stay within memory limits.
SPACY_MODEL = "en_core_web_lg" if importlib.util.find_spec("en_core_web_lg") else "en_core_web_sm"

nlp_engine = NlpEngineProvider(nlp_configuration={
    "nlp_engine_name": "spacy",
    "models": [{"lang_code": "en", "model_name": SPACY_MODEL}],
}).create_engine()
analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
anonymizer = AnonymizerEngine()

def redact_pii_presidio(text: str) -> str:
    # 1. Analyze text for entities (EMAIL_ADDRESS, PHONE_NUMBER, PERSON, LOCATION, etc.)
    results = analyzer.analyze(
        text=text,
        entities=["EMAIL_ADDRESS", "PHONE_NUMBER", "PERSON", "LOCATION", "UK_NINO"],
        language="en"
    )
    
    # 2. Anonymize/Redact detected entities
    anonymized_result = anonymizer.anonymize(
        text=text,
        analyzer_results=results
    )
    return anonymized_result.text
