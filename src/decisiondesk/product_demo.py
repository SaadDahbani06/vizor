"""End-to-end demo: dataset -> processing -> FRED context -> forecast -> scenarios -> ranking ->
conversational explanation, for both demonstration businesses.

  python -m decisiondesk.product_demo              # Claude if an Anthropic credential exists, else offline analyst
  python -m decisiondesk.product_demo --offline    # force the rule-based offline analyst
"""
from __future__ import annotations

import argparse
import time

from dotenv import load_dotenv

from .agent.agent import DecisionAgent
from .agent.tools import BusinessSession

CONVERSATIONS = {
    "restaurant": ["What should I do if my food costs increase 15%?",
                   "What happens if demand becomes more sensitive to price increases?",
                   "What do current U.S. food-price indicators show?"],
    "retail": ["What should I do with a $40,000 inventory budget?",
               "What if I increase the budget to $45,000?"],
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--business", choices=list(CONVERSATIONS), nargs="*",
                        default=list(CONVERSATIONS))
    args = parser.parse_args()
    load_dotenv(".env")
    for key in args.business:
        session = BusinessSession.from_demo(key)
        agent = DecisionAgent(session, mode="offline" if args.offline else None)
        md = session.metadata
        print(f"\n{'=' * 100}\n{md['business_name']} · {md['city']}, {md['state']} · data: "
              f"{session.data_label} · analyst mode: {agent.mode}\n{'=' * 100}")
        for q in CONVERSATIONS[key]:
            t = time.time()
            reply = agent.ask(q)
            calls = ", ".join(f"{c['name']}(run {c['output']['run_id']})" for c in reply.tool_calls
                              if "output" in c) or "none"
            print(f"\nUSER: {q}\nTOOLS: {calls} · {time.time() - t:.0f}s · grounded: "
                  f"{reply.grounded}{'' if reply.grounded else ' ' + str(reply.ungrounded)}")
            print(f"ANALYST:\n{reply.text}")


if __name__ == "__main__":
    main()
