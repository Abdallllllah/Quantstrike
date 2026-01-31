"""Quick verification script for the multi-subject AI tutor architecture."""

def test_imports():
    """Test all module imports."""
    print("Testing imports...")
    try:
        from app.db import SupabaseClient, User, Subject, Class, Message
        print("  [OK] Database models")
        
        from app.rag import RAGRegistry, SubjectRAGConfig
        print("  [OK] RAG registry")
        
        from app.controller import IntentDetector, Intent
        print("  [OK] Controller/Intent")
        
        from app.whatsapp import whatsapp_router
        print("  [OK] WhatsApp router")
        
        from app.context import ContextManager
        print("  [OK] Context manager")
        
        return True
    except Exception as e:
        print(f"  [FAIL] Import failed: {e}")
        return False


def test_intent_detector():
    """Test intent detection."""
    print("\nTesting intent detector...")
    from app.controller.intent import IntentDetector, Intent
    
    detector = IntentDetector()
    
    test_cases = [
        ("Explain momentum", Intent.QUESTION),
        ("Give me 5 practice questions on friction", Intent.PRACTICE),
        ("mark my answer: the answer is 42", Intent.MARK),
        ("/help", Intent.HELP),
        ("What is photosynthesis?", Intent.QUESTION),
    ]
    
    all_passed = True
    for message, expected in test_cases:
        detected, _ = detector.detect(message)
        status = "[OK]" if detected == expected else "[FAIL]"
        if detected != expected:
            all_passed = False
        print(f"  {status} '{message[:30]}...' -> {detected.value} (expected: {expected.value})")
    
    return all_passed


def test_subject_configs():
    """Test subject RAG configurations."""
    print("\nTesting subject configs...")
    from app.rag.base import MATH_CONFIG, PHYSICS_CONFIG, CHEMISTRY_CONFIG
    
    configs = [MATH_CONFIG, PHYSICS_CONFIG, CHEMISTRY_CONFIG]
    for config in configs:
        print(f"  [OK] {config.subject_name} config loaded")
        # Verify prompts are generated
        assert len(config.get_qa_prompt()) > 100
        assert len(config.get_practice_prompt()) > 100
        assert len(config.get_marking_prompt()) > 100
    
    return True


def test_pydantic_models():
    """Test Pydantic models."""
    print("\nTesting Pydantic models...")
    from uuid import uuid4
    from datetime import datetime
    from app.db.models import User, Subject, Message, ConversationContext
    
    # Test User model
    user = User(
        id=uuid4(),
        phone_number="+1234567890",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    print(f"  [OK] User model: {user.phone_number}")
    
    # Test ConversationContext
    ctx = ConversationContext(
        user_id=uuid4(),
        subject_id=uuid4(),
        class_id=uuid4(),
    )
    formatted = ctx.format_history()
    print(f"  [OK] ConversationContext: history formatted")
    
    return True


def main():
    print("=" * 50)
    print("Multi-Subject AI Tutor - Verification")
    print("=" * 50 + "\n")
    
    results = []
    
    results.append(("Imports", test_imports()))
    results.append(("Intent Detector", test_intent_detector()))
    results.append(("Subject Configs", test_subject_configs()))
    results.append(("Pydantic Models", test_pydantic_models()))
    
    print("\n" + "=" * 50)
    print("RESULTS")
    print("=" * 50)
    
    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"  {name}: {status}")
    
    print("\n" + ("All tests passed!" if all_passed else "Some tests failed"))
    
    print("\n" + "-" * 50)
    print("NOTE: Supabase connectivity requires .env setup")
    print("Run migrations in Supabase SQL Editor first")
    print("-" * 50)


if __name__ == "__main__":
    main()
