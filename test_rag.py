import httpx
import sys

queries = [
    "What is potential energy?",
    "What is kinematics?",
    "Explain electric current",
]

for q in queries:
    r = httpx.post(
        "http://127.0.0.1:8000/api/rag",
        json={
            "user_id": "test",
            "message": q,
            "school_id": "9eb0c0a2-b1cc-4ee2-af8a-df23ab96c432",
            "subject": "physics",
            "class": "Grade 10",
        },
        timeout=60,
    )
    data = r.json()
    print(f"Q: {q}")
    print(f"A: {data['response']}")
    print(f"[{len(data['response'])} chars]")
    print("=" * 60)
    sys.stdout.flush()
