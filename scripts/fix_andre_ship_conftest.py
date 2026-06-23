"""Fix test files by moving andre_ship path setup into conftest.py."""
import pathlib


TEST_DIR = pathlib.Path("/opt/data/tests")


def clean_test_source(content: str) -> str:
    lines = content.split("\n")
    cleaned = [line for line in lines if line.strip() != "sys.path.insert(0, '/opt/data')"]
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
