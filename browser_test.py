"""
Browser automation script using the Intuned stealth Chromium.

Usage:
  python browser_test.py --task "Find the latest pricing for the Oculus Quest 3"
  python browser_test.py --task "..." --no-stealth

Environment variables required:
  INTUNED_STEALTH_CHROMIUM_PATH  Path to the Intuned stealth Chromium binary
  OPENAI_API_KEY                 OpenAI API key for the browser-use agent
"""

import argparse
import asyncio
import os

from browser_use import Agent, BrowserProfile, BrowserSession
from browser_use.llm.openai.chat import ChatOpenAI
from dotenv import load_dotenv

from providers.intuned_provider import cleanup_session, create_session

load_dotenv()


async def main(stealth: bool = True, task: str = "Check the score of the last 3 patriots games"):
    """Run browser automation with the Intuned stealth Chromium."""
    process, cdp_url, user_data_dir = create_session(stealth=stealth)

    llm = ChatOpenAI(
        model="gpt-5-mini",
        api_key=os.environ.get("OPENAI_API_KEY"),
        temperature=0.0,
    )

    profile = BrowserProfile(cdp_url=cdp_url, keep_alive=True)
    browser_session = BrowserSession(browser_profile=profile)

    agent = Agent(
        task=task,
        llm=llm,
        browser_session=browser_session,
        use_vision=False,
    )

    history = await agent.run(max_steps=40)

    execution_successful = False
    error_message = None

    if hasattr(history, "is_done") and hasattr(history, "is_successful"):
        is_done_attr = getattr(history, "is_done")
        is_successful_attr = getattr(history, "is_successful")
        is_done_value = is_done_attr() if callable(is_done_attr) else is_done_attr
        is_successful_value = is_successful_attr() if callable(is_successful_attr) else is_successful_attr
        execution_successful = is_done_value and is_successful_value
        if not execution_successful and hasattr(history, "errors"):
            errors = history.errors()
            if errors:
                error_message = "; ".join(str(e) for e in errors)
    else:
        if hasattr(history, "has_errors"):
            has_errors_attr = getattr(history, "has_errors")
            has_errors_value = has_errors_attr() if callable(has_errors_attr) else has_errors_attr
            execution_successful = not has_errors_value
            if has_errors_value and hasattr(history, "errors"):
                errors = history.errors()
                if errors:
                    error_message = "; ".join(str(e) for e in errors)

    final_message = ""
    try:
        if hasattr(history, "final_result"):
            final_message = history.final_result()
        else:
            if hasattr(history, "extracted_content"):
                contents = history.extracted_content()
                if contents:
                    final_message = contents[-1]
    except Exception as e:
        final_message = "Could not extract final message"
        error_message = str(e) if not error_message else f"{error_message}; {e}"

    try:
        await browser_session.stop()
    except Exception as e:
        print(f"Error stopping browser session: {e}")

    cleanup_session(process, user_data_dir)

    return final_message, None, execution_successful, error_message


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run browser automation with Intuned stealth Chromium")
    parser.add_argument(
        "--no-stealth",
        action="store_true",
        help="Pass stealth=False to the provider (binary is always stealth build)",
    )
    parser.add_argument(
        "--task",
        type=str,
        default="Check the score of the last 3 patriots games",
        help="Task for the browser agent",
    )
    args = parser.parse_args()

    final_result, _, execution_successful, error_message = asyncio.run(
        main(stealth=not args.no_stealth, task=args.task)
    )
    print("\n=== Final Results ===")
    print("Execution Successful:", execution_successful)
    print("Final Result:", final_result)
    if error_message:
        print("Error Message:", error_message)
