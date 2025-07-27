import yaml
import requests
from datetime import datetime, timezone
from email.utils import format_datetime
from feedgen.feed import FeedGenerator
import os
import xml.etree.ElementTree as ET


# --- Load settings from YAML ---
def load_settings(path):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

# --- Fetch works from OpenAlex ---
def fetch_openalex_works(params, email, max_results=50):
    url = "https://api.openalex.org/works"
    params["per_page"] = max_results
    params["mailto"] = email
    response = requests.get(url, params=params)
    print("Query URL:", response.url)
    response.raise_for_status()
    return response.json()["results"]

# --- Extract impact score ---
journal_cache = {}

def get_journal_impact_score(work, email):
    try:
        journal_id_url = work.get("primary_location", {}).get("source", {}).get("id")
        if not journal_id_url:
            return 0.0

        journal_id = journal_id_url.rsplit("/", 1)[-1]
        if journal_id in journal_cache:
            return journal_cache[journal_id]

        response = requests.get(f"https://api.openalex.org/sources/{journal_id}",
                                params={"mailto": email})
        response.raise_for_status()
        data = response.json()
        impact = float(data.get("summary_stats", {}).get("2yr_mean_citedness", 0.0))

        journal_cache[journal_id] = impact
        return impact
    except Exception as e:
        print(f"Error fetching journal impact: {e}")
        return 0.0

# --- Generate feed ---
def generate_rss_feed(feed_title, items, output_file):
    fg = FeedGenerator()
    fg.title(feed_title)
    fg.link(href="https://openalex.org", rel="alternate")
    fg.link(href=f"https://openalex.org/feeds/{os.path.basename(output_file)}", rel="self", type="application/rss+xml")
    fg.description(f"Literature feed generated from OpenAlex for: {feed_title}")
    fg.language("en")

    if not items:
        print(f"⚠️ No items to write for feed: {feed_title}")

    for work in items:
        raw_title = work.get("title", "No title")
        title = raw_title.replace("<", "&lt;").replace(">", "&gt;")
        doi_raw = work.get("doi", "")
        abstract = work.get("abstract", "No abstract available")
        pub_date = work.get("publication_date", "2024-01-01")

        authors = ", ".join([a["author"]["display_name"] for a in work.get("authorships", [])])
        if doi_raw.startswith("http"):
            link_url = doi_raw
        elif doi_raw:
            link_url = f"https://doi.org/{doi_raw}"
        else:
            link_url = "https://openalex.org"

        entry = fg.add_entry()
        entry.id(link_url)
        entry.title(title)
        entry.link(href=link_url)
        entry.description(f"<b>Authors:</b> {authors}<br/><br/><b>Abstract:</b><br/>{abstract}")
        try:
            dt = datetime.fromisoformat(pub_date).replace(tzinfo=timezone.utc)
            entry.pubDate(format_datetime(dt))
        except Exception as e:
            print(f"⚠️ Skipping invalid pubDate for '{title}': {pub_date} ({e})")
    fg.rss_file(output_file)

# --- Main logic ---
def main():
    settings_path = os.path.join("settings", "openalex_settings.yaml")
    feeds_dir = os.path.join("feeds")
    os.makedirs(feeds_dir, exist_ok=True)

    config = load_settings(settings_path)
    email = config["email"]
    impact_threshold = config.get("impact_threshold", 0.0)

    for key, query in config["queries"].items():
        query_type = query.get("type", "keyword")
        feed_name = query["feed_name"]
        output_path = os.path.join(feeds_dir, f"{feed_name}.xml")

        if query_type == "keyword":
            search_str = query["search"]
            params = {
                "search": search_str,
                "filter": "from_publication_date:2023-01-01",
                "sort": "publication_date:desc"
            }
            works = fetch_openalex_works(params, email)

        elif query_type == "authors":
            author_ids = [a["id"] for a in query["authors"]]
            print("Tracking authors:")
            for a in query["authors"]:
                print(f"  - {a['name']} ({a['id']})")
            author_filter = "|".join(author_ids)
            params = {
                "filter": f"from_publication_date:2023-01-01,author.id:{author_filter}",
                "sort": "publication_date:desc"
            }
            works = fetch_openalex_works(params, email)

        else:
            print(f"Unknown query type: {query_type}, skipping.")
            continue
        
        filtered_works = [w for w in works if get_journal_impact_score(w, email) >= impact_threshold]
        # Sort by publication date (descending)
        filtered_works.sort(
            key=lambda w: w.get("publication_date", "1900-01-01"), reverse=True
        )
        print(f"[{feed_name}] Returned {len(works)} works, {len(filtered_works)} passed impact filter")
        generate_rss_feed(feed_name, filtered_works, output_path)
        print(f"✅ Generated feed: {output_path}\n")

if __name__ == "__main__":
    main()
