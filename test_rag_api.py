import requests
import json
import argparse
import sys

def test_rag_api(user_id, message, subject, class_level, url="http://localhost:8000/api/rag"):
    """
    Test the clean RAG API endpoint.
    """
    payload = {
        "user_id": user_id,
        "message": message,
        "subject": subject,
        "class": class_level
    }
    
    print(f"Testing RAG API at {url}...")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    print("-" * 30)
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        
        result = response.json()
        print("Success! Response received:")
        print(f"Answer: {result.get('response')}")
        print(f"Subject: {result.get('subject')}")
        print(f"Class: {result.get('class')}")
        print(f"Sources: {', '.join(result.get('sources', []))}")
        
    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to the server. Is it running?")
        print("Run 'uv run python -m app.main' or 'uvicorn app.main:app --reload' in another terminal.")
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e.response.status_code}")
        print(f"Details: {e.response.text}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test the clean RAG integration API.")
    parser.add_argument("--user", default="test-user-001", help="User ID")
    parser.add_argument("--msg", default="What is momentum?", help="Message/Query")
    parser.add_argument("--subject", default="physics", help="Subject slug or ID")
    parser.add_argument("--class", dest="class_level", default="grade10", help="Class identifier")
    parser.add_argument("--url", default="http://localhost:8000/api/rag", help="API URL")
    
    args = parser.parse_args()
    
    test_rag_api(args.user, args.msg, args.subject, args.class_level, args.url)
