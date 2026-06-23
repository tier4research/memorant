"""Fix test files by moving andre_ship path setup into conftest.py."""
import pathlib


TEST_DIR = pathlib.Path("/opt/data/tests")


def clean_test_source(content: str) -> str:
    lines = content.split("\n")
    cleaned = [line for line in lines if line.strip() != "sys.path.insert(0, '/opt/data')"]
    if "sys." in "\n".join(cleaned) and not any(line.strip() == "import sys" for line in cleaned):
        insert_at = 0
        if cleaned and cleaned[0].strip() == "from __future__ import annotations":
            insert_at = 1
            if len(cleaned) > 1 and cleaned[1].strip() == "":
                insert_at = 2
        cleaned.insert(insert_at, "import sys")
    return "\n".join(cleaned)


def main() -> None:
    for fname in ["test_andre_hermes_integration_contract.py", "test_andre_persona_capability.py"]:
        path = TEST_DIR / fname
        path.write_text(clean_test_source(path.read_text()))
        print(f"CLEANED {fname}")

    conftest = TEST_DIR / "conftest.py"
    conftest.write_text("import sys\nsys.path.insert(0, '/opt/data')\n")
    print("CREATED conftest.py")


if __name__ == "__main__":
    main()
