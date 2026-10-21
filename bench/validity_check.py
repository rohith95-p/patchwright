"""Does Nano's triage refuse tasks that are not specific bug reports, and accept real ones?

  python -m bench.validity_check          (about a dozen small Nano calls, a few cents)

The task lists are small and written by hand, so treat the result as a sanity check, not a measured accuracy.
"""
from patchwright.agent import triage
from patchwright.llm import LLM

SNAPSHOT = ("FILES:\nstats.py\ntest_visible.py\n--- stats.py ---\ndef average(nums):\n    return sum(nums) / (len(nums) - 1)\n"
            "--- test_visible.py ---\nfrom stats import average\n\ndef test_single_zero():\n    assert average([0, 0]) == 0\n")
INVALID = ["find all the bugs and fix them", "i wanna know all the issues", "add a median function", "explain how average works",
           "make it faster", "refactor this code to be cleaner", "something is wrong with it",
           "review my code for security problems", "write documentation for the project", "update all dependencies to the latest versions"]
VALID = ["average([2, 4, 6]) returns 6.0 but should be 4.0", "average([]) raises ZeroDivisionError; it should return 0.0",
         "average([1, 2]) gives 2.0 and I expected 1.5",
         "Calling average with a list of one number fails: average([5]) raises ZeroDivisionError but should return 5.0",
         "average(None) raises AttributeError but should raise TypeError"]


def main() -> None:
    llm = LLM()
    refused = sum(1 for t in INVALID if triage(llm, SNAPSHOT, t)["valid"] is False)
    accepted = sum(1 for t in VALID if triage(llm, SNAPSHOT, t)["valid"] is True)
    print(f"refused {refused}/{len(INVALID)} invalid tasks; accepted {accepted}/{len(VALID)} valid tasks; "
          f"tokens {llm.usage.prompt_tokens + llm.usage.completion_tokens:,}")


if __name__ == "__main__":
    main()
