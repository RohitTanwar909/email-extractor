from flask import Flask, request, render_template_string
import requests
import re
from bs4 import BeautifulSoup
from urllib.parse import urlparse

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


EMAIL_REGEX = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

PHONE_REGEX = re.compile(
    r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)"
)

SOCIAL_DOMAINS = {
    "linkedin.com": "LinkedIn",
    "twitter.com": "Twitter",
    "x.com": "X",
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "youtube.com": "YouTube",
    "github.com": "GitHub",
}


def normalize_url(url):
    url = url.strip()

    if not url:
        return None

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return None

    if not parsed.netloc:
        return None

    return url


def clean_text(value):
    if not value:
        return ""

    return re.sub(r"\s+", " ", value).strip()


def unique(items):
    result = []

    for item in items:
        item = clean_text(item)

        if item and item not in result:
            result.append(item)

    return result


def extract_data(html, final_url):
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-visible/noisy elements
    for element in soup(["script", "style", "noscript", "svg"]):
        element.decompose()

    visible_text = clean_text(soup.get_text(" ", strip=True))

    # ---------------------------------------------------------
    # Title
    # ---------------------------------------------------------

    title = ""

    if soup.title:
        title = clean_text(soup.title.get_text())

    # ---------------------------------------------------------
    # Meta description
    # ---------------------------------------------------------

    description = ""

    meta_description = soup.find(
        "meta",
        attrs={"name": re.compile("^description$", re.I)}
    )

    if meta_description:
        description = clean_text(
            meta_description.get("content", "")
        )

    # ---------------------------------------------------------
    # OpenGraph information
    # ---------------------------------------------------------

    og_title = ""
    og_description = ""
    og_image = ""

    meta_og_title = soup.find(
        "meta",
        attrs={"property": "og:title"}
    )

    meta_og_description = soup.find(
        "meta",
        attrs={"property": "og:description"}
    )

    meta_og_image = soup.find(
        "meta",
        attrs={"property": "og:image"}
    )

    if meta_og_title:
        og_title = clean_text(meta_og_title.get("content", ""))

    if meta_og_description:
        og_description = clean_text(
            meta_og_description.get("content", "")
        )

    if meta_og_image:
        og_image = clean_text(
            meta_og_image.get("content", "")
        )

    # ---------------------------------------------------------
    # Emails
    # ---------------------------------------------------------

    emails = set(
        match.lower()
        for match in EMAIL_REGEX.findall(html)
    )

    # Also inspect mailto links
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()

        if href.lower().startswith("mailto:"):
            email = href[7:].split("?")[0].strip()

            if EMAIL_REGEX.fullmatch(email):
                emails.add(email.lower())

    # ---------------------------------------------------------
    # Phone numbers
    # ---------------------------------------------------------

    phones = set()

    # tel: links are stronger evidence than arbitrary numbers
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()

        if href.lower().startswith("tel:"):
            phone = href[4:].split("?")[0].strip()

            if phone:
                phones.add(phone)

    # Find phone-like strings in returned HTML/text
    for match in PHONE_REGEX.findall(visible_text):
        cleaned = clean_text(match)

        digits = re.sub(r"\D", "", cleaned)

        # Avoid treating years or tiny numbers as phone numbers
        if len(digits) >= 8:
            phones.add(cleaned)

    # ---------------------------------------------------------
    # Name candidates
    # ---------------------------------------------------------

    names = []

    # OpenGraph title is often useful
    if og_title:
        names.append(og_title)

    # H1
    for h1 in soup.find_all("h1"):
        text = clean_text(h1.get_text())

        if text:
            names.append(text)

    # ---------------------------------------------------------
    # Links
    # ---------------------------------------------------------

    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()

        if not href.startswith(("http://", "https://")):
            continue

        links.append(href)

    links = unique(links)

    # ---------------------------------------------------------
    # Social profiles
    # ---------------------------------------------------------

    social_profiles = []

    for link in links:
        parsed = urlparse(link)
        hostname = parsed.netloc.lower()

        if hostname.startswith("www."):
            hostname = hostname[4:]

        for domain, name in SOCIAL_DOMAINS.items():
            if hostname == domain or hostname.endswith("." + domain):
                social_profiles.append({
                    "name": name,
                    "url": link
                })
                break

    # Remove duplicate social URLs
    seen_social = set()
    clean_social = []

    for item in social_profiles:
        if item["url"] not in seen_social:
            seen_social.add(item["url"])
            clean_social.append(item)

    # ---------------------------------------------------------
    # Website links
    # ---------------------------------------------------------

    websites = []

    source_domain = urlparse(final_url).netloc.lower()

    for link in links:
        domain = urlparse(link).netloc.lower()

        if not domain:
            continue

        if domain == source_domain:
            continue

        is_social = any(
            domain == d or domain.endswith("." + d)
            for d in SOCIAL_DOMAINS
        )

        if not is_social:
            websites.append(link)

    websites = unique(websites)

    # ---------------------------------------------------------
    # JSON-LD structured data
    # ---------------------------------------------------------

    structured_data = []

    for script in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"}
    ):
        if script.string:
            text = clean_text(script.string)

            if text:
                structured_data.append(text[:5000])

    return {
        "title": title,
        "description": description,
        "og_title": og_title,
        "og_description": og_description,
        "og_image": og_image,
        "emails": sorted(emails),
        "phones": sorted(phones),
        "names": unique(names),
        "social_profiles": clean_social,
        "websites": websites[:50],
        "structured_data": structured_data[:10],
        "page_text_preview": visible_text[:3000],
    }


HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>Public Profile Inspector</title>

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            font-family: Arial, sans-serif;
            background: #f4f6f8;
            color: #17202a;
        }

        .container {
            width: min(1100px, 94%);
            margin: 40px auto;
        }

        .card {
            background: white;
            border-radius: 14px;
            padding: 25px;
            margin-bottom: 20px;
            box-shadow: 0 5px 25px rgba(0,0,0,.07);
        }

        h1 {
            margin-top: 0;
        }

        .subtitle {
            color: #667085;
            line-height: 1.6;
        }

        form {
            display: flex;
            gap: 10px;
            margin-top: 20px;
        }

        input[type="url"] {
            flex: 1;
            padding: 14px;
            border: 1px solid #ccd2d8;
            border-radius: 8px;
            font-size: 16px;
        }

        button {
            border: 0;
            padding: 14px 22px;
            border-radius: 8px;
            background: #111827;
            color: white;
            cursor: pointer;
            font-size: 15px;
        }

        button:hover {
            opacity: .9;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 20px;
        }

        .item {
            background: #f8fafc;
            padding: 15px;
            border-radius: 8px;
            margin-top: 10px;
            overflow-wrap: anywhere;
        }

        .label {
            font-size: 13px;
            font-weight: bold;
            color: #667085;
            text-transform: uppercase;
            margin-bottom: 6px;
        }

        .value {
            font-size: 16px;
        }

        .success {
            color: #087443;
        }

        .warning {
            color: #a15c00;
        }

        .error {
            background: #fff1f2;
            color: #b42318;
            padding: 15px;
            border-radius: 8px;
        }

        ul {
            padding-left: 20px;
        }

        li {
            margin: 8px 0;
            overflow-wrap: anywhere;
        }

        a {
            color: #175cd3;
            text-decoration: none;
        }

        pre {
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            background: #111827;
            color: #e5e7eb;
            padding: 15px;
            border-radius: 8px;
            max-height: 500px;
            overflow: auto;
        }

        .notice {
            font-size: 13px;
            color: #667085;
            margin-top: 15px;
        }

        @media (max-width: 700px) {
            form {
                flex-direction: column;
            }

            .grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>

<body>

<div class="container">

    <div class="card">

        <h1>Public Profile Inspector</h1>

        <p class="subtitle">
            Enter a publicly accessible profile or webpage URL.
            The application analyzes only the HTML returned by the website.
        </p>

        <form method="POST">

            <input
                type="url"
                name="url"
                placeholder="https://example.com/profile"
                value="{{ url or '' }}"
                required
            >

            <button type="submit">
                Inspect
            </button>

        </form>

        <div class="notice">
            This tool does not log in, bypass authentication, defeat CAPTCHA,
            or attempt to access private/restricted information.
        </div>

    </div>


    {% if error %}

    <div class="card">
        <div class="error">
            {{ error }}
        </div>
    </div>

    {% endif %}


    {% if data %}

    <div class="card">

        <h2>Basic Information</h2>

        <div class="grid">

            <div>
                <div class="label">Page title</div>
                <div class="item">{{ data.title or "Not found" }}</div>
            </div>

            <div>
                <div class="label">OpenGraph title</div>
                <div class="item">{{ data.og_title or "Not found" }}</div>
            </div>

            <div>
                <div class="label">Description</div>
                <div class="item">
                    {{ data.description or data.og_description or "Not found" }}
                </div>
            </div>

            <div>
                <div class="label">Detected name/title candidates</div>

                <div class="item">

                    {% if data.names %}

                        {% for name in data.names %}
                            <div>{{ name }}</div>
                        {% endfor %}

                    {% else %}

                        Not found

                    {% endif %}

                </div>

            </div>

        </div>

    </div>


    <div class="card">

        <h2>Public Contact Information</h2>

        <p class="notice">
            These are values that were actually present in the returned page.
        </p>

        <div class="grid">

            <div>

                <div class="label">Email addresses</div>

                <div class="item">

                    {% if data.emails %}

                        {% for email in data.emails %}
                            <div>
                                <a href="mailto:{{ email }}">
                                    {{ email }}
                                </a>
                            </div>
                        {% endfor %}

                    {% else %}

                        <span class="warning">
                            No email found in returned HTML
                        </span>

                    {% endif %}

                </div>

            </div>


            <div>

                <div class="label">Phone numbers</div>

                <div class="item">

                    {% if data.phones %}

                        {% for phone in data.phones %}
                            <div>{{ phone }}</div>
                        {% endfor %}

                    {% else %}

                        <span class="warning">
                            No phone number found in returned HTML
                        </span>

                    {% endif %}

                </div>

            </div>

        </div>

    </div>


    <div class="card">

        <h2>Social Profiles</h2>

        {% if data.social_profiles %}

            <ul>

            {% for profile in data.social_profiles %}

                <li>
                    <strong>{{ profile.name }}:</strong>
                    <a href="{{ profile.url }}" target="_blank" rel="noopener">
                        {{ profile.url }}
                    </a>
                </li>

            {% endfor %}

            </ul>

        {% else %}

            <p>No social profiles found.</p>

        {% endif %}

    </div>


    <div class="card">

        <h2>External Websites</h2>

        {% if data.websites %}

            <ul>

            {% for website in data.websites %}

                <li>
                    <a href="{{ website }}" target="_blank" rel="noopener">
                        {{ website }}
                    </a>
                </li>

            {% endfor %}

            </ul>

        {% else %}

            <p>No external websites found.</p>

        {% endif %}

    </div>


    {% if data.structured_data %}

    <div class="card">

        <h2>Structured Data</h2>

        {% for item in data.structured_data %}

            <pre>{{ item }}</pre>

        {% endfor %}

    </div>

    {% endif %}


    <div class="card">

        <h2>Page Text Preview</h2>

        <pre>{{ data.page_text_preview }}</pre>

    </div>

    {% endif %}

</div>

</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def index():

    data = None
    error = None
    url = ""

    if request.method == "POST":

        url = request.form.get("url", "").strip()

        normalized = normalize_url(url)

        if not normalized:
            error = "Please enter a valid HTTP/HTTPS URL."

        else:

            try:

                response = requests.get(
                    normalized,
                    headers=HEADERS,
                    timeout=15,
                    allow_redirects=True
                )

                if response.status_code >= 400:
                    error = (
                        f"The website returned HTTP "
                        f"{response.status_code}."
                    )

                else:

                    content_type = response.headers.get(
                        "Content-Type",
                        ""
                    ).lower()

                    if "text/html" not in content_type:
                        error = (
                            "The supplied URL did not return an HTML page."
                        )

                    else:

                        data = extract_data(
                            response.text,
                            response.url
                        )

                        url = response.url

            except requests.exceptions.Timeout:
                error = "The website took too long to respond."

            except requests.exceptions.ConnectionError:
                error = "Could not connect to the website."

            except requests.exceptions.RequestException as exc:
                error = f"Request failed: {exc}"

            except Exception as exc:
                error = f"Unexpected error: {exc}"

    return render_template_string(
        HTML,
        data=data,
        error=error,
        url=url
    )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=6000,
        debug=False
    )
