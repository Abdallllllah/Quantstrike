"""
Base classes and interfaces for Subject RAG configurations.
"""
from typing import Optional, Callable, Any
from pydantic import BaseModel, Field, ConfigDict


class SubjectRAGConfig(BaseModel):
    """Configuration for a subject-specific RAG pipeline."""
    model_config = ConfigDict(from_attributes=True)
    
    subject_id: str
    subject_name: str
    slug: str
    
    # Prompt customization
    prompt_template: Optional[str] = None
    system_context: str = "You are a helpful tutor."
    
    # Retrieval settings
    retrieval_k: int = 5
    similarity_threshold: float = 0.3
    
    # Document types this subject uses
    doc_types: list[str] = Field(default_factory=lambda: ["notes", "examples"])
    
    def get_qa_prompt(self) -> str:
        """Get the Q&A prompt template for this subject."""
        # Validate that template has required variables for LangChain StuffDocumentsChain
        if self.prompt_template:
            if '{context}' in self.prompt_template and '{question}' in self.prompt_template:
                return self.prompt_template
            # Invalid template - fall through to default
        
        subject_name = self.subject_name
        # Use string concatenation to avoid f-string escaping issues with LangChain variables
        return f"""You are a {subject_name} tutor. You must ONLY answer using the context provided below from the student's uploaded documents.

STRICT RULES (YOU MUST FOLLOW THESE WITHOUT EXCEPTION):
1. ONLY use information that is explicitly stated in the Context below.
2. Do NOT use any prior knowledge, training data, or general knowledge to answer.
3. If the Context below is empty, or does not contain information relevant to the question, you MUST respond EXACTLY with: "This topic isn't covered in your course materials yet. Try asking about something from your syllabus, or let your teacher know so they can add the right resources for you!"
4. Do NOT make up, infer, or fabricate any information that is not directly found in the Context.
5. Do NOT supplement the Context with your own knowledge, even if you know the answer.
6. Every claim in your answer must be traceable to the Context provided.

FORMATTING RULES:
- Do NOT use asterisks, bold, or any markdown formatting in your response.
- Write in plain text only.
- Use numbered lists (1, 2, 3) for steps, not bullet points.
- Keep your language natural and conversational.

HOW TO ANSWER (only when the Context contains relevant information):
1. Answer the student's question directly using ONLY the context provided.
2. If the question involves calculations, show your work step by step using formulas from the context.
3. Extract the exact numbers from the QUESTION (not from examples in the context).
4. Apply formulas and methods from the context using those numbers.
5. Include units where applicable.

Context: """ + "{context}" + """

Question: """ + "{question}" + """

Answer (remember: ONLY use information from the Context above, never your own knowledge):"""
    
    def get_practice_prompt(self) -> str:
        """Get the practice question generation prompt."""
        return f"""You are an expert {self.subject_name} exam question writer.

IMPORTANT FORMATTING RULES:
- Do NOT use asterisks, bold, or any markdown formatting.
- Write in plain text only.
- Use simple numbered lists.

Generate practice questions based on this topic: """ + "{question}" + """
Difficulty: {difficulty}
Number of questions: {count}

For each question, provide:
1. The question number and text
2. Mark allocation (1-5 marks)
3. A brief expected answer

Format:
Q1. [Question text] [X marks]
Expected Answer: [Brief answer]

Q2. [Question text] [X marks]
Expected Answer: [Brief answer]

Context: """ + "{context}" + """

Practice Questions:"""
    
    def get_marking_prompt(self) -> str:
        """Get the answer marking prompt."""
        return f"""You are a {self.subject_name} exam marker. Mark the student's answer carefully.

IMPORTANT FORMATTING RULES:
- Do NOT use asterisks, bold, or any markdown formatting.
- Write in plain text only.
- Use simple numbered lists where needed.

QUESTION: """ + "{question}" + """
STUDENT'S ANSWER: {student_answer}
MAXIMUM MARKS: {max_marks}

MARKING INSTRUCTIONS:
1. Verify all calculations in the student's answer.
2. Compare the final answer to the correct answer.
3. Deduct marks for: wrong answers, calculation errors, missing steps, wrong formulas, missing units.
4. Give partial credit for correct method even if arithmetic is wrong.

Provide your response in this format:

MARKS AWARDED: [X]/{max_marks}

CALCULATION CHECK:
[Your verification of the student's work]

FEEDBACK:
[What was done well and what needs improvement]

ERRORS FOUND:
[List any errors, or "No errors found"]

MODEL ANSWER:
[The correct solution]

Context: """ + "{context}" + """

Marking:"""


# Default configurations for common subjects
MATH_CONFIG = SubjectRAGConfig(
    subject_id="",  # Will be set from DB
    subject_name="Mathematics",
    slug="math",
    system_context="You are an expert mathematics tutor. Focus on step-by-step calculations, clear explanations of formulas, and building intuition for mathematical concepts.",
    retrieval_k=5,
    doc_types=["notes", "examples", "worked_solutions"],
)

PHYSICS_CONFIG = SubjectRAGConfig(
    subject_id="",
    subject_name="Physics",
    slug="physics",
    system_context="You are an expert physics tutor. Explain concepts with real-world examples, include relevant equations, and help students understand the underlying principles.",
    retrieval_k=5,
    doc_types=["notes", "examples", "experiments"],
)

CHEMISTRY_CONFIG = SubjectRAGConfig(
    subject_id="",
    subject_name="Chemistry",
    slug="chemistry",
    system_context="You are an expert chemistry tutor. Explain reactions, molecular structures, and chemical principles clearly with appropriate diagrams and equations.",
    retrieval_k=5,
    doc_types=["notes", "equations", "reactions"],
)

# Mapping of slugs to default configs
DEFAULT_CONFIGS = {
    "math": MATH_CONFIG,
    "physics": PHYSICS_CONFIG,
    "chemistry": CHEMISTRY_CONFIG,
}
