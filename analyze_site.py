import sys
import json
import llm
import logging
from playwright.sync_api import sync_playwright

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_URL = sys.argv[1]
SEARCH_TEST_QUERY = "test"

model = llm.get_model("github/gpt-4o")
results = {
    "pages": {},
    "search_detected": False,
    "search_details": None,
    "search_test_results": None
}
has_errors = False

def analyze_page(url, html):
    """Send page HTML to LLM to classify and detect operations."""
    global has_errors
    
    prompt = f"""
You are analyzing a website page. Identify:

1. Page type (homepage, product page, cart, search results, etc.).
2. All user actions (buttons, forms, links), with their purpose.
3. If there is a search function, give form action URL and parameter names.

IMPORTANT: Return ONLY valid JSON with no markdown formatting, no code blocks, no explanations, and no additional text.

Return JSON with keys: page_type, actions[], search_form{{action, params[]}}.

Example response format:
{{"page_type": "homepage", "actions": [], "search_form": null}}
"""
    try:
        logging.info(f"Analyzing page: {url}")
        resp = model.prompt(prompt + "\n\nHTML:\n" + html[:5000])
        response_text = resp.text().strip()
        logging.debug(f"LLM response: {response_text}")
        
        # Try to extract JSON from markdown code blocks if present
        if "```json" in response_text:
            # Extract content between ```json and ```
            start = response_text.find("```json") + 7
            end = response_text.find("```", start)
            if end != -1:
                response_text = response_text[start:end].strip()
                logging.debug(f"Extracted JSON from code block: {response_text}")
        
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse JSON from LLM response: {e}")
        logging.error(f"Raw LLM response: {response_text}")
        has_errors = True
        return {"error": "Failed to parse LLM output"}
    except Exception as e:
        logging.error(f"Error in analyze_page: {e}")
        has_errors = True
        return {"error": f"Failed to analyze page: {str(e)}"}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()

    # Visit base page
    page.goto(BASE_URL)
    html = page.content()
    analysis = analyze_page(BASE_URL, html)
    results["pages"][BASE_URL] = analysis

    # If search form detected, run a test search
    if analysis.get("search_form"):
        results["search_detected"] = True
        results["search_details"] = analysis["search_form"]

        try:
            # Fill search box and submit
            param_name = analysis["search_form"]["params"][0]
            selector = f'input[name="{param_name}"]'
            logging.info(f"Looking for search input with selector: {selector}")
            if page.locator(selector).count() > 0:
                logging.info(f"Found search input, filling with: {SEARCH_TEST_QUERY}")
                page.fill(selector, SEARCH_TEST_QUERY)
                page.keyboard.press("Enter")
                page.wait_for_load_state("networkidle")
                search_html = page.content()
                search_analysis = analyze_page(BASE_URL + " (search results)", search_html)
                results["search_test_results"] = search_analysis
            else:
                logging.warning(f"No search input found with selector: {selector}")
        except Exception as e:
            logging.error(f"Error during search form interaction: {e}")
            logging.error(f"Search form details: {analysis.get('search_form')}")
            has_errors = True
            results["search_test_results"] = {"error": str(e)}

    browser.close()

# Save results
with open("results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)

# Exit with error code if any errors occurred
if has_errors:
    logging.error("Script completed with errors")
    sys.exit(1)
else:
    logging.info("Script completed successfully")
    sys.exit(0)
