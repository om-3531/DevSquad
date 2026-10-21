from backend.agents.coder import CoderAgent

coder = CoderAgent()

result = coder.generate_code(
    task="Write a Python function to check whether a number is even or odd.",
    implementation_plan=(
        "1. Define check_even_odd. "
        "2. Use the modulo operator. "
        "3. Return Even or Odd."
    ),
)

print("\n===== CODER AGENT OUTPUT =====\n")
print(result)