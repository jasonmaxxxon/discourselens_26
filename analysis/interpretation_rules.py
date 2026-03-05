SYSTEM_PROMPT = (
    "You are NOT a sociologist.\n"
    "You must NOT use academic theory names.\n"
    "You must describe what is present, not why it exists.\n"
    "All outputs MUST be in Traditional Chinese (zh-Hant). Do NOT use English except proper nouns."
)

FORBIDDEN_TERMS = [
    "capitalis",
    "oppression",
    "structural",
    "hegemony",
    "marx",
    "patriarchy",
    "資本主義",
    "壓迫",
    "結構性",
    "霸權",
    "馬克思",
    "父權",
]

FORBIDDEN_CAUSAL = [
    "because",
    "therefore",
    "indicates",
    "reflects",
    "underlying",
    "因為",
    "導致",
    "反映",
    "代表",
    "顯示其內心",
    "本質上",
]
