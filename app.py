
import os
import re
import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS


app = Flask(__name__)
CORS(app)


# ============================================================
# CONFIGURATION
# ============================================================

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

# Optional:
# API_KEY=your-secret-key
#
# If API_KEY is not configured, API authentication is disabled.
API_KEY = os.getenv("API_KEY", "").strip()

MAX_RESPONSE_SIZE = 5 * 1024 * 1024  # 5 MB


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


# ============================================================
# REGEX
# ============================================================

EMAIL_REGEX = re.compile(
    r"\b[A-Za-z0-9._%+-]+"
    r"@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)


# This regex is deliberately NOT used against arbitrary page text.
#
# Generic phone regexes cause false positives such as:
#
# 1997 - 2001
# 2001 - 2003
# 2024 - 2025
#
# For safety/accuracy, this application primarily trusts:
#
# tel:+123456789
#
# links.
TEL_ALLOWED_REGEX = re.compile(
    r"^\+?[0-9][0-9\s().-]{6,20}$"
)


# ============================================================
# URL HELPERS
# ============================================================

def normalize_url(url):
    """
    Normalize and validate a user-supplied URL.
    """

    if not isinstance(url, str):
        return None

    url = url.strip()

    if not url:
        return None

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    if not parsed.netloc:
        return None

    # Do not allow credentials inside URLs.
    if parsed.username or parsed.password:
        return None

    return url


def hostname_is_private(hostname):
    """
    Prevent requests to localhost/private/internal addresses.

    This is important because the application accepts arbitrary URLs.
    """

    if not hostname:
        return True

    hostname = hostname.strip().lower()

    if hostname == "localhost":
        return True

    if hostname.endswith(".localhost"):
        return True

    if hostname.endswith(".local"):
        return True

    # Direct IP address
    try:
        ip = ipaddress.ip_address(hostname)

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True

        return False

    except ValueError:
        pass

    # Resolve hostname and check resulting addresses.
    try:
        addresses = socket.getaddrinfo(
            hostname,
            None,
            proto=socket.IPPROTO_TCP
        )

        for item in addresses:
            address = item[4][0]

            try:
                ip = ipaddress.ip_address(address)

                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                    or ip.is_unspecified
                ):
                    return True

            except ValueError:
                continue

    except Exception:
        # If DNS resolution fails, requests will report the error.
        pass

    return False


def validate_target_url(url):
    """
    Validate URL and prevent obvious SSRF targets.
    """

    normalized = normalize_url(url)

    if not normalized:
        return None, "Invalid HTTP/HTTPS URL."

    parsed = urlparse(normalized)

    if hostname_is_private(parsed.hostname):
        return None, "Private, local, or internal addresses are not allowed."

    return normalized, None


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)

    return re.sub(r"\s+", " ", value).strip()


def unique(items):
    result = []
    seen = set()

    for item in items:
        item = clean_text(item)

        if not item:
            continue

        if item not in seen:
            seen.add(item)
            result.append(item)

    return result


def normalize_hostname(hostname):
    if not hostname:
        return ""

    hostname = hostname.lower().strip()

    if hostname.startswith("www."):
        hostname = hostname[4:]

    return hostname


def same_domain(url1, url2):
    try:
        host1 = normalize_hostname(urlparse(url1).netloc)
        host2 = normalize_hostname(urlparse(url2).netloc)

        return host1 == host2

    except Exception:
        return False


# ============================================================
# LINKEDIN HELPERS
# ============================================================

def is_linkedin_domain(url):
    try:
        hostname = normalize_hostname(urlparse(url).netloc)

        return (
            hostname == "linkedin.com"
            or hostname.endswith(".linkedin.com")
        )

    except Exception:
        return False


def is_linkedin_person_profile(url):
    """
    True only for URLs resembling:

    https://www.linkedin.com/in/username

    Does NOT accept:

    /login
    /signup
    /jobs
    /learning
    /company
    /posts
    /top-content
    /pub/dir
    etc.
    """

    try:
        parsed = urlparse(url)

        hostname = normalize_hostname(parsed.netloc)

        if hostname != "linkedin.com":
            return False

        path = parsed.path.rstrip("/")

        return bool(
            re.fullmatch(
                r"/in/[A-Za-z0-9_-]+",
                path,
                re.IGNORECASE
            )
        )

    except Exception:
        return False


def is_linkedin_company(url):
    try:
        parsed = urlparse(url)

        hostname = normalize_hostname(parsed.netloc)

        if hostname != "linkedin.com":
            return False

        path = parsed.path.rstrip("/")

        return bool(
            re.fullmatch(
                r"/company/[A-Za-z0-9_-]+",
                path,
                re.IGNORECASE
            )
        )

    except Exception:
        return False


def is_linkedin_post(url):
    try:
        parsed = urlparse(url)

        hostname = normalize_hostname(parsed.netloc)

        if hostname != "linkedin.com":
            return False

        return parsed.path.startswith("/posts/")

    except Exception:
        return False


def clean_linkedin_profile_url(url):
    """
    Remove tracking query parameters from a LinkedIn profile URL.
    """

    if not is_linkedin_person_profile(url):
        return None

    parsed = urlparse(url)

    return urlunparse(
        (
            "https",
            "www.linkedin.com",
            parsed.path.rstrip("/"),
            "",
            "",
            ""
        )
    )


# ============================================================
# PHONE EXTRACTION
# ============================================================

def extract_phones(soup):
    """
    IMPORTANT:

    We intentionally do NOT search arbitrary page text for phone-like
    numbers.

    That prevents false positives such as:

        1997 - 2001
        2001 - 2003
        2024 - 2025

    The safest public HTML signal is an explicit tel: link.
    """

    phones = set()

    for link in soup.find_all("a", href=True):

        href = clean_text(link.get("href", ""))

        if not href:
            continue

        if not href.lower().startswith("tel:"):
            continue

        phone = href[4:].split("?", 1)[0].strip()

        if not phone:
            continue

        # Decode basic HTML entities if any.
        phone = clean_text(phone)

        # Validate the actual telephone value.
        if not TEL_ALLOWED_REGEX.fullmatch(phone):
            continue

        digits = re.sub(r"\D", "", phone)

        # E.164/general international range.
        if not (8 <= len(digits) <= 15):
            continue

        phones.add(phone)

    return sorted(phones)


# ============================================================
# EMAIL EXTRACTION
# ============================================================

def extract_emails(soup, html):
    """
    Extract emails actually exposed in the returned HTML.

    Sources:
      - mailto links
      - visible/raw HTML email strings
    """

    emails = set()

    # mailto links
    for link in soup.find_all("a", href=True):

        href = clean_text(link.get("href", ""))

        if href.lower().startswith("mailto:"):

            email = href[7:].split("?", 1)[0].strip()

            if EMAIL_REGEX.fullmatch(email):
                emails.add(email.lower())

    # Raw HTML
    for email in EMAIL_REGEX.findall(html):
        emails.add(email.lower())

    return sorted(emails)


# ============================================================
# NAME EXTRACTION
# ============================================================

def extract_name_candidates(soup, title, og_title):
    names = []

    # h1 values
    for h1 in soup.find_all("h1"):

        value = clean_text(h1.get_text(" ", strip=True))

        if value:
            names.append(value)

    # OpenGraph title
    if og_title:
        names.append(og_title)

    # Page title
    if title:
        names.append(title)

    return unique(names)[:20]


# ============================================================
# METADATA
# ============================================================

def get_meta_content(soup, *, name=None, property_name=None):

    tag = None

    if name:
        tag = soup.find(
            "meta",
            attrs={
                "name": re.compile(
                    "^" + re.escape(name) + "$",
                    re.IGNORECASE
                )
            }
        )

    elif property_name:
        tag = soup.find(
            "meta",
            attrs={
                "property": re.compile(
                    "^" + re.escape(property_name) + "$",
                    re.IGNORECASE
                )
            }
        )

    if not tag:
        return ""

    return clean_text(tag.get("content", ""))


# ============================================================
# LINK EXTRACTION
# ============================================================

def extract_links(soup, source_url):
    """
    Extract useful links while filtering navigation noise.
    """

    all_links = []
    linkedin_profiles = []
    linkedin_companies = []
    linkedin_posts = []
    external_websites = []

    source_domain = normalize_hostname(
        urlparse(source_url).netloc
    )

    for anchor in soup.find_all("a", href=True):

        href = anchor.get("href", "").strip()

        if not href:
            continue

        # Only HTTP(S)
        if not href.startswith(("http://", "https://")):
            continue

        try:
            parsed = urlparse(href)

        except Exception:
            continue

        if not parsed.netloc:
            continue

        # LinkedIn
        if is_linkedin_person_profile(href):

            cleaned = clean_linkedin_profile_url(href)

            if cleaned:
                linkedin_profiles.append(cleaned)

            continue

        if is_linkedin_company(href):

            linkedin_companies.append(href)

            continue

        if is_linkedin_post(href):

            linkedin_posts.append(href)

            continue

        # Ignore other LinkedIn navigation URLs.
        if is_linkedin_domain(href):
            continue

        # External website
        domain = normalize_hostname(parsed.netloc)

        if domain and domain != source_domain:

            external_websites.append(href)

        all_links.append(href)

    return {
        "linkedin_profiles": unique(linkedin_profiles),
        "linkedin_companies": unique(linkedin_companies),
        "linkedin_posts": unique(linkedin_posts),
        "external_websites": unique(external_websites),
    }


# ============================================================
# SOCIAL PROFILE EXTRACTION
# ============================================================

SOCIAL_DOMAINS = {
    "twitter.com": "Twitter",
    "x.com": "X",
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "youtube.com": "YouTube",
    "github.com": "GitHub",
    "tiktok.com": "TikTok",
}


def extract_social_profiles(soup):
    profiles = []
    seen = set()

    for anchor in soup.find_all("a", href=True):

        href = anchor.get("href", "").strip()

        if not href.startswith(("http://", "https://")):
            continue

        try:
            parsed = urlparse(href)
        except Exception:
            continue

        hostname = normalize_hostname(parsed.netloc)

        if not hostname:
            continue

        platform = None

        for domain, name in SOCIAL_DOMAINS.items():

            if (
                hostname == domain
                or hostname.endswith("." + domain)
            ):
                platform = name
                break

        if not platform:
            continue

        if href in seen:
            continue

        seen.add(href)

        profiles.append({
            "platform": platform,
            "url": href
        })

    return profiles


# ============================================================
# STRUCTURED DATA
# ============================================================

def extract_structured_data(soup):
    data = []

    for script in soup.find_all(
        "script",
        attrs={
            "type": re.compile(
                r"application/ld\+json",
                re.IGNORECASE
            )
        }
    ):

        content = script.string or script.get_text()

        content = clean_text(content)

        if not content:
            continue

        data.append(content[:10000])

    return data[:10]


# ============================================================
# MAIN EXTRACTION
# ============================================================

def extract_data(html, final_url):
    soup = BeautifulSoup(html, "html.parser")

    # Create a copy for visible text.
    text_soup = BeautifulSoup(html, "html.parser")

    for element in text_soup(
        ["script", "style", "noscript", "svg", "template"]
    ):
        element.decompose()

    visible_text = clean_text(
        text_soup.get_text(" ", strip=True)
    )

    # --------------------------------------------------------
    # Basic title
    # --------------------------------------------------------

    title = ""

    if soup.title:
        title = clean_text(
            soup.title.get_text(" ", strip=True)
        )

    # --------------------------------------------------------
    # Meta description
    # --------------------------------------------------------

    description = get_meta_content(
        soup,
        name="description"
    )

    # --------------------------------------------------------
    # OpenGraph
    # --------------------------------------------------------

    og_title = get_meta_content(
        soup,
        property_name="og:title"
    )

    og_description = get_meta_content(
        soup,
        property_name="og:description"
    )

    og_image = get_meta_content(
        soup,
        property_name="og:image"
    )

    # --------------------------------------------------------
    # Contact
    # --------------------------------------------------------

    emails = extract_emails(
        soup,
        html
    )

    phones = extract_phones(
        soup
    )

    # --------------------------------------------------------
    # Names
    # --------------------------------------------------------

    names = extract_name_candidates(
        soup,
        title,
        og_title
    )

    # --------------------------------------------------------
    # Links
    # --------------------------------------------------------

    links = extract_links(
        soup,
        final_url
    )

    # --------------------------------------------------------
    # Social profiles
    # --------------------------------------------------------

    social_profiles = extract_social_profiles(
        soup
    )

    # --------------------------------------------------------
    # Structured data
    # --------------------------------------------------------

    structured_data = extract_structured_data(
        soup
    )

    # --------------------------------------------------------
    # LinkedIn main profile
    # --------------------------------------------------------

    linkedin_profile = None

    if is_linkedin_person_profile(final_url):
        linkedin_profile = clean_linkedin_profile_url(
            final_url
        )

    elif links["linkedin_profiles"]:
        linkedin_profile = links["linkedin_profiles"][0]

    # --------------------------------------------------------
    # Return result
    # --------------------------------------------------------

    return {
        "success": True,

        "source": {
            "url": final_url,
            "data_source": "public_http_html_only"
        },

        "profile": {
            "name_candidates": names,
            "title": title,
            "description": description,
            "og_title": og_title,
            "og_description": og_description,
            "og_image": og_image
        },

        "contact": {
            "emails": emails,
            "phones": phones
        },

        "linkedin": {
            "profile": linkedin_profile,
            "profiles_found": links["linkedin_profiles"][:20],
            "companies_found": links["linkedin_companies"][:20],
            "posts_found": links["linkedin_posts"][:20]
        },

        "social_profiles": social_profiles[:50],

        "external_websites": links[
            "external_websites"
        ][:50],

        "structured_data": structured_data,

        "page_text_preview": visible_text[:5000]
    }


# ============================================================
# API AUTHENTICATION
# ============================================================

def check_api_key():

    # Authentication disabled if API_KEY isn't configured.
    if not API_KEY:
        return True

    supplied_key = (
        request.headers.get("X-API-Key")
        or request.args.get("api_key")
    )

    if supplied_key == API_KEY:
        return True

    return False


# ============================================================
# FETCH PAGE
# ============================================================

def fetch_page(url):

    validated_url, error = validate_target_url(url)

    if error:
        raise ValueError(error)

    response = requests.get(
        validated_url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
        stream=True
    )

    # Check HTTP status.
    if response.status_code >= 400:
        raise ValueError(
            f"Target website returned HTTP "
            f"{response.status_code}."
        )

    content_type = response.headers.get(
        "Content-Type",
        ""
    ).lower()

    if "text/html" not in content_type:
        raise ValueError(
            "The supplied URL did not return an HTML page."
        )

    # Read with a maximum size.
    content = bytearray()

    for chunk in response.iter_content(
        chunk_size=65536
    ):

        if not chunk:
            continue

        content.extend(chunk)

        if len(content) > MAX_RESPONSE_SIZE:
            raise ValueError(
                "The HTML response is larger than the allowed limit."
            )

    response.close()

    encoding = response.encoding or "utf-8"

    html = bytes(content).decode(
        encoding,
        errors="replace"
    )

    return html, response.url


# ============================================================
# API ENDPOINT
# ============================================================

@app.route("/api/extract", methods=["GET", "POST"])
def api_extract():

    if not check_api_key():

        return jsonify({
            "success": False,
            "error": "Invalid or missing API key."
        }), 401

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    if request.method == "GET":

        url = request.args.get(
            "url",
            ""
        ).strip()

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    else:

        url = ""

        # JSON body
        if request.is_json:

            body = request.get_json(
                silent=True
            ) or {}

            url = str(
                body.get("url", "")
            ).strip()

        # Form body
        if not url:

            url = request.form.get(
                "url",
                ""
            ).strip()

    if not url:

        return jsonify({
            "success": False,
            "error": "Missing required parameter: url"
        }), 400

    try:

        html, final_url = fetch_page(
            url
        )

        result = extract_data(
            html,
            final_url
        )

        return jsonify(result)

    except requests.exceptions.Timeout:

        return jsonify({
            "success": False,
            "error": "The target website timed out."
        }), 504

    except requests.exceptions.ConnectionError:

        return jsonify({
            "success": False,
            "error": "Could not connect to the target website."
        }), 502

    except requests.exceptions.RequestException as exc:

        return jsonify({
            "success": False,
            "error": f"Request failed: {str(exc)}"
        }), 502

    except ValueError as exc:

        return jsonify({
            "success": False,
            "error": str(exc)
        }), 400

    except Exception as exc:

        return jsonify({
            "success": False,
            "error": f"Unexpected error: {str(exc)}"
        }), 500


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health", methods=["GET"])
def health():

    return jsonify({
        "success": True,
        "service": "Public Profile Inspector",
        "status": "ok"
    })


# ============================================================
# WEB INTERFACE
# ============================================================

HTML = r"""
<!DOCTYPE html>

<html lang="en">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

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

h2 {
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

.warning {
    color: #a15c00;
}

.error {
    background: #fff1f2;
    color: #b42318;
    padding: 15px;
    border-radius: 8px;
}

.success {
    color: #087443;
}

.notice {
    font-size: 13px;
    color: #667085;
    margin-top: 15px;
    line-height: 1.5;
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

.badge {
    display: inline-block;
    padding: 5px 9px;
    background: #eef4ff;
    border-radius: 6px;
    font-size: 12px;
    margin-bottom: 8px;
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
This tool does not log in, bypass authentication,
defeat CAPTCHA, or attempt to access private/restricted
information.
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

<div class="label">
Page title
</div>

<div class="item">
{{ data.profile.title or "Not found" }}
</div>

</div>


<div>

<div class="label">
OpenGraph title
</div>

<div class="item">
{{ data.profile.og_title or "Not found" }}
</div>

</div>


<div>

<div class="label">
Description
</div>

<div class="item">

{{ data.profile.description
   or data.profile.og_description
   or "Not found" }}

</div>

</div>


<div>

<div class="label">
Name candidates
</div>

<div class="item">

{% if data.profile.name_candidates %}

{% for name in data.profile.name_candidates %}

<div>
{{ name }}
</div>

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
Only contact information actually exposed by the
returned HTML is displayed.
</p>


<div class="grid">


<div>

<div class="label">
Email addresses
</div>

<div class="item">

{% if data.contact.emails %}

{% for email in data.contact.emails %}

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

<div class="label">
Phone numbers
</div>

<div class="item">

{% if data.contact.phones %}

{% for phone in data.contact.phones %}

<div>
{{ phone }}
</div>

{% endfor %}

{% else %}

<span class="warning">
No phone number exposed through a telephone link
</span>

{% endif %}

</div>

</div>


</div>

</div>


<div class="card">

<h2>LinkedIn</h2>

{% if data.linkedin.profile %}

<div class="item">

<div class="badge">
Personal profile
</div>

<br>

<a
href="{{ data.linkedin.profile }}"
target="_blank"
rel="noopener noreferrer"
>

{{ data.linkedin.profile }}

</a>

</div>

{% else %}

<p>
No LinkedIn personal profile URL identified.
</p>

{% endif %}


{% if data.linkedin.companies_found %}

<h3>Companies</h3>

<ul>

{% for link in data.linkedin.companies_found %}

<li>

<a
href="{{ link }}"
target="_blank"
rel="noopener noreferrer"
>

{{ link }}

</a>

</li>

{% endfor %}

</ul>

{% endif %}


{% if data.linkedin.posts_found %}

<h3>Posts</h3>

<ul>

{% for link in data.linkedin.posts_found[:20] %}

<li>

<a
href="{{ link }}"
target="_blank"
rel="noopener noreferrer"
>

{{ link }}

</a>

</li>

{% endfor %}

</ul>

{% endif %}

</div>


<div class="card">

<h2>Social Profiles</h2>

{% if data.social_profiles %}

<ul>

{% for profile in data.social_profiles %}

<li>

<strong>
{{ profile.platform }}:
</strong>

<a
href="{{ profile.url }}"
target="_blank"
rel="noopener noreferrer"
>

{{ profile.url }}

</a>

</li>

{% endfor %}

</ul>

{% else %}

<p>
No supported social profiles found.
</p>

{% endif %}

</div>


<div class="card">

<h2>External Websites</h2>

{% if data.external_websites %}

<ul>

{% for website in data.external_websites %}

<li>

<a
href="{{ website }}"
target="_blank"
rel="noopener noreferrer"
>

{{ website }}

</a>

</li>

{% endfor %}

</ul>

{% else %}

<p>
No external websites found.
</p>

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


# ============================================================
# WEB ROUTE
# ============================================================

@app.route("/", methods=["GET", "POST"])
def index():

    data = None
    error = None
    url = ""

    if request.method == "POST":

        url = request.form.get(
            "url",
            ""
        ).strip()

        if not url:

            error = "Please enter a URL."

        else:

            try:

                html, final_url = fetch_page(
                    url
                )

                data = extract_data(
                    html,
                    final_url
                )

                url = final_url

            except requests.exceptions.Timeout:

                error = (
                    "The website took too long to respond."
                )

            except requests.exceptions.ConnectionError:

                error = (
                    "Could not connect to the website."
                )

            except requests.exceptions.RequestException as exc:

                error = (
                    f"Request failed: {str(exc)}"
                )

            except ValueError as exc:

                error = str(exc)

            except Exception as exc:

                error = (
                    f"Unexpected error: {str(exc)}"
                )

    return render_template_string(
        HTML,
        data=data,
        error=error,
        url=url
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "6000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
```
