import os
search_terms = ["информац", "помощ"]

def search_files(directory):
    for root, _, files in os.walk(directory):
        if "__pycache__" in root or ".git" in root:
            continue
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                        for i, line in enumerate(lines):
                            for term in search_terms:
                                if term.lower() in line.lower():
                                    print(f"{path}:{i+1}: {line.strip()}")
                except Exception as e:
                    pass

search_files("bot")
