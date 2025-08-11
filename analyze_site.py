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
You are analyzing a website page. Look carefully for search functionality.

Analyze this HTML and identify:
1. Page type (homepage, product page, cart, search results, etc.)
2. All interactive elements (forms, buttons, links) and their purpose
3. IMPORTANT: Search functionality - look for:
   - Input fields with type="search" or names like "search", "query", "q"
   - Form elements that might be used for searching
   - Submit buttons associated with search inputs

For search forms, provide:
- The form's action URL (or current page if no action specified)
- All relevant input parameter names (especially search-related ones)

Return ONLY valid JSON with no markdown formatting, code blocks, or explanations.

Required JSON structure:
{{
  "page_type": "string",
  "actions": [
    {{"type": "form|button|link", "purpose": "description", "details": "additional info"}}
  ],
  "search_form": {{
    "action": "url_or_path",
    "params": ["param1", "param2"]
  }} OR null if no search found
}}

Be very thorough in looking for search functionality. Even if it's not obvious, check for any input fields that could be used for search.
"""
    try:
        logging.info(f"Analyzing page: {url}")
        # Send more HTML content for better analysis
        html_snippet = html[:8000]  # Increased from 5000
        resp = model.prompt(prompt + "\n\nHTML to analyze:\n" + html_snippet)
        response_text = resp.text().strip()
        logging.debug(f"LLM response: {response_text}")
        
        # Try to extract JSON from markdown code blocks if present
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            end = response_text.find("```", start)
            if end != -1:
                response_text = response_text[start:end].strip()
                logging.debug(f"Extracted JSON from code block: {response_text}")
        
        result = json.loads(response_text)
        
        # Log the analysis result for debugging
        if result.get("search_form"):
            logging.info(f"Search form detected: {result['search_form']}")
        else:
            logging.info("No search form detected in this analysis")
            
        return result
        
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse JSON from LLM response: {e}")
        logging.error(f"Raw LLM response (first 500 chars): {response_text[:500]}")
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
                
                # Wait for navigation to start, then complete
                with page.expect_navigation(timeout=30000):
                    page.keyboard.press("Enter")
                
                # Additional wait to ensure page is fully loaded
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(2000)  # Wait 2 seconds for any dynamic content
                
                try:
                    search_html = page.content()
                    search_analysis = analyze_page(BASE_URL + " (search results)", search_html)
                    results["search_test_results"] = search_analysis
                except Exception as content_error:
                    logging.error(f"Failed to get search results content: {content_error}")
                    # Try one more time after a longer wait
                    page.wait_for_timeout(3000)
                    try:
                        search_html = page.content()
                        search_analysis = analyze_page(BASE_URL + " (search results)", search_html)
                        results["search_test_results"] = search_analysis
                    except Exception as retry_error:
                        logging.error(f"Retry failed: {retry_error}")
                        has_errors = True
                        results["search_test_results"] = {"error": f"Failed to capture search results: {str(retry_error)}"}
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
