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
    retrieval_k: int = 10
    similarity_threshold: float = 0.2
    
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
        
        # Physics-specific SI Standardization Protocol (GCE/BACC compliance)
        si_protocol = ""
        if self.slug == "physics":
            si_protocol = """
SI STANDARDIZATION & PRE-CALCULATION PROTOCOL (MANDATORY FOR ALL CALCULATIONS):

Before performing ANY mathematical operation, you MUST follow this protocol:

Step 1 - UNIT INTERCEPTION:
Convert ALL variables into Standard SI Base Units (m, kg, s, A, K, mol) BEFORE calculating.
Treat any non-SI or prefixed unit (km, cm, g, min, mA, uF) as a compliance error that must be corrected first.
Say: "First, we must standardize your units to [Unit] to align with GCE/BACC requirements."

Step 2 - ABSOLUTE TEMPERATURE RULE:
NEVER use Celsius in calculations. If Celsius is detected, immediately convert: T(K) = t(C) + 273.15
Only Kelvin (K) is acceptable for thermodynamic and gas law formulas.

Step 3 - SCIENTIFIC NOTATION:
Express ALL standardized values in scientific notation (A x 10^n) before substituting into formulas.
Example: 500 nm -> 5.0 x 10^-7 m, 200 g -> 2.0 x 10^-1 kg

DIMENSION CONVERSION REFERENCE:
- Length: Convert all prefixes (k, c, m, u, n) to meters (m)
- Mass: Convert to kilograms (kg). Example: 200 g = 2.0 x 10^-1 kg
- Time: Convert minutes/hours to seconds (s)
- Electromagnetism: mA -> A, uF -> F, kOhm -> Ohm using powers of 10
- Volume: 1 L = 10^-3 m^3, 1 cm^3 = 10^-6 m^3

CALCULATION FORMAT:
1. Show the standardization steps explicitly with conversions
2. Write the formula
3. Substitute the standardized values
4. Solve step by step
5. State the final answer with correct SI units
"""

        # Use string concatenation to avoid f-string escaping issues with LangChain variables
        return f"""You are a friendly {subject_name} tutor for high school students. Keep answers short and clear.

RULES:
1. Use ONLY the course materials below. Do NOT use your own knowledge.
2. If related concepts are in the materials, use them to answer.
3. Only refuse if the materials have ZERO connection to the question. Refusal: "This topic isn't covered in your course materials yet. Try asking about something from your syllabus, or let your teacher know so they can add the right resources for you!"
4. Never say "context", "passage", "document", or "text". Talk like a tutor.
5. Answer confidently. No hedging.

RESPONSE LENGTH (VERY IMPORTANT):
- For "what is" or definition questions: 2-3 sentences MAX. Give the definition and one example, then stop.
- For "explain" questions: 3-5 sentences. Brief explanation with a key example.
- For calculation questions: show the formula, then the step-by-step working. Be thorough here.
- For "list" or "compare" questions: use a short numbered list.
- NEVER pad your answer with extra information the student did not ask for.
- Get to the point immediately. No preambles like "Great question!" or "Let me explain..."

FORMAT:
- Plain text only. No asterisks, no bold, no markdown.
- Numbered lists (1, 2, 3) for steps.
- Include units in calculations.
{si_protocol}
The student's course materials:
""" + "{context}" + """

Student's question: """ + "{question}" + """

Your answer:"""
    
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
