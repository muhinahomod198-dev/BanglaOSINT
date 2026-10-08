from flask import Flask, render_template, request, jsonify
import requests
import ipaddress
import subprocess
import re
import os
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image
import phonenumbers
from phonenumbers import geocoder, carrier, timezone, PhoneNumberType

app = Flask(__name__)

SESSION = requests.Session()

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

PUBLIC_SITES = {
    "TikTok": "https://www.tiktok.com/@{username}",
    "Facebook": "https://www.facebook.com/{username}",
    "Instagram": "https://www.instagram.com/{username}/",
    "X / Twitter": "https://x.com/{username}",
    "YouTube": "https://www.youtube.com/@{username}",
    "Telegram": "https://t.me/{username}",
    "GitHub": "https://github.com/{username}",
    "GitLab": "https://gitlab.com/{username}",
    "Reddit": "https://www.reddit.com/user/{username}/",
    "Codeberg": "https://codeberg.org/{username}",
    "Keybase": "https://keybase.io/{username}",
    "Pinterest": "https://www.pinterest.com/{username}/",
    "Twitch": "https://www.twitch.tv/{username}",
    "Dev.to": "https://dev.to/{username}",
    "Medium": "https://medium.com/@{username}",
    "Mastodon": "https://mastodon.social/@{username}",
}


# =========================================================
# IP FUNCTIONS
# =========================================================

def valid_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def public_ip(ip):
    obj = ipaddress.ip_address(ip)

    return not (
        obj.is_private
        or obj.is_loopback
        or obj.is_link_local
        or obj.is_reserved
        or obj.is_multicast
    )


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/ip")
def ip_lookup():

    ip = request.args.get("ip", "").strip()

    if not valid_ip(ip):
        return jsonify({
            "ok": False,
            "error": "Invalid IP address"
        }), 400

    if not public_ip(ip):
        return jsonify({
            "ok": False,
            "error": "Private/reserved IP cannot be publicly geolocated"
        }), 400

    try:

        response = SESSION.get(
            f"https://ipwho.is/{ip}",
            timeout=10
        )

        data = response.json()

        if not data.get("success", False):
            return jsonify({
                "ok": False,
                "error": "Public IP data unavailable"
            }), 404

        connection = data.get("connection") or {}

        return jsonify({
            "ok": True,
            "ip": ip,
            "country": data.get("country"),
            "country_code": data.get("country_code"),
            "region": data.get("region"),
            "city": data.get("city"),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "timezone": data.get("timezone", {}).get("id"),
            "isp": connection.get("isp"),
            "org": connection.get("org"),
            "asn": connection.get("asn")
        })

    except Exception as error:

        return jsonify({
            "ok": False,
            "error": str(error)
        }), 503


# =========================================================
# TRACEROUTE
# =========================================================

def geolocate_hop(hop):

    ip = hop.get("ip")

    if not ip:
        return hop

    if not valid_ip(ip):
        return hop

    if not public_ip(ip):
        return hop

    try:

        response = SESSION.get(
            f"https://ipwho.is/{ip}",
            timeout=5
        )

        data = response.json()

        if data.get("success"):

            hop["country"] = data.get("country")
            hop["city"] = data.get("city")
            hop["lat"] = data.get("latitude")
            hop["lon"] = data.get("longitude")
            hop["isp"] = (data.get("connection") or {}).get("isp")

    except Exception:
        pass

    return hop


@app.route("/api/trace")
def trace():

    target = request.args.get("ip", "").strip()

    if not valid_ip(target):
        return jsonify({
            "ok": False,
            "error": "Invalid IP address"
        }), 400

    try:

        command = [
            "traceroute",
            "-n",
            "-w", "1",
            "-q", "1",
            "-m", "20",
            target
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=35
        )

        hops = []

        for line in result.stdout.splitlines():

            line = line.strip()

            match = re.match(
                r"^(\d+)\s+(.+)$",
                line
            )

            if not match:
                continue

            hop_number = int(match.group(1))
            rest = match.group(2)

            ip_match = re.search(
                r"(\d{1,3}(?:\.\d{1,3}){3})",
                rest
            )

            hop_ip = (
                ip_match.group(1)
                if ip_match
                else None
            )

            latency_match = re.search(
                r"(\d+(?:\.\d+)?)\s*ms",
                rest
            )

            latency = (
                float(latency_match.group(1))
                if latency_match
                else None
            )

            hops.append({
                "hop": hop_number,
                "ip": hop_ip,
                "latency_ms": latency
            })

        with ThreadPoolExecutor(max_workers=5) as executor:

            futures = [
                executor.submit(
                    geolocate_hop,
                    hop
                )
                for hop in hops
            ]

            enriched = [
                future.result()
                for future in as_completed(futures)
            ]

        enriched.sort(
            key=lambda x: x["hop"]
        )

        return jsonify({
            "ok": True,
            "target": target,
            "hops": enriched,
            "raw": result.stdout
        })

    except subprocess.TimeoutExpired:

        return jsonify({
            "ok": False,
            "error": "Traceroute timed out"
        }), 504

    except FileNotFoundError:

        return jsonify({
            "ok": False,
            "error": "traceroute is not installed"
        }), 500


# =========================================================
# USERNAME
# =========================================================

def check_username(site, url):

    headers = {
        "User-Agent":
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/130 Safari/537.36"
    }

    try:

        response = SESSION.get(
            url,
            timeout=8,
            allow_redirects=True,
            headers=headers
        )

        return {
            "site": site,
            "url": url,
            "status": response.status_code,
            "final_url": response.url,
            "exists": response.status_code == 200
        }

    except requests.RequestException:

        return {
            "site": site,
            "url": url,
            "status": 0,
            "final_url": url,
            "exists": False
        }


@app.route("/api/username")
def username_lookup():

    username = (
        request.args
        .get("username", "")
        .strip()
        .lstrip("@")
    )

    if not re.fullmatch(
        r"[A-Za-z0-9._-]{2,40}",
        username
    ):

        return jsonify({
            "ok": False,
            "error":
            "Use 2-40 characters: letters, numbers, . _ -"
        }), 400

    jobs = [
        (
            site,
            template.format(username=username)
        )
        for site, template in PUBLIC_SITES.items()
    ]

    results = []

    with ThreadPoolExecutor(max_workers=8) as executor:

        futures = [
            executor.submit(
                check_username,
                site,
                url
            )
            for site, url in jobs
        ]

        for future in as_completed(futures):
            results.append(
                future.result()
            )

    results.sort(
        key=lambda x: x["site"]
    )

    return jsonify({
        "ok": True,
        "username": username,
        "checked": len(results),
        "results": results
    })


# =========================================================
# PHONE INSPECTION
# =========================================================

def phone_type_name(number_type):

    mapping = {
        PhoneNumberType.MOBILE: "Mobile",
        PhoneNumberType.FIXED_LINE: "Fixed line",
        PhoneNumberType.FIXED_LINE_OR_MOBILE:
            "Fixed line / Mobile",
        PhoneNumberType.VOIP: "VoIP",
        PhoneNumberType.PAGER: "Pager",
        PhoneNumberType.UAN: "UAN",
        PhoneNumberType.VOICEMAIL: "Voicemail",
        PhoneNumberType.PREMIUM_RATE: "Premium rate",
        PhoneNumberType.TOLL_FREE: "Toll free",
        PhoneNumberType.SHARED_COST: "Shared cost",
        PhoneNumberType.PERSONAL_NUMBER:
            "Personal number",
        PhoneNumberType.UNKNOWN: "Unknown"
    }

    return mapping.get(
        number_type,
        "Unknown"
    )


@app.route("/api/phone")
def phone_lookup():

    number = request.args.get(
        "number",
        ""
    ).strip()

    region = request.args.get(
        "region",
        "BD"
    ).strip().upper()

    if not number:

        return jsonify({
            "ok": False,
            "error": "Phone number required"
        }), 400

    try:

        parsed = phonenumbers.parse(
            number,
            region
        )

        possible = phonenumbers.is_possible_number(
            parsed
        )

        valid = phonenumbers.is_valid_number(
            parsed
        )

        e164 = phonenumbers.format_number(
            parsed,
            phonenumbers.PhoneNumberFormat.E164
        )

        international = phonenumbers.format_number(
            parsed,
            phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )

        country = geocoder.description_for_number(
            parsed,
            "en"
        )

        carrier_name = carrier.name_for_number(
            parsed,
            "en"
        )

        zones = timezone.time_zones_for_number(
            parsed
        )

        number_type = phone_type_name(
            phonenumbers.number_type(parsed)
        )

        # Public web search links.
        # এগুলো search-engine links; account ownership প্রমাণ করে না।

        google_url = (
            "https://www.google.com/search?q="
            + requests.utils.quote(
                '"' + e164 + '"'
            )
        )

        bing_url = (
            "https://www.bing.com/search?q="
            + requests.utils.quote(
                '"' + e164 + '"'
            )
        )

        duck_url = (
            "https://duckduckgo.com/?q="
            + requests.utils.quote(
                '"' + e164 + '"'
            )
        )

        return jsonify({

            "ok": True,

            "input": number,

            "region": region,

            "e164": e164,

            "international": international,

            "country": country,

            "valid": valid,

            "possible": possible,

            "type": number_type,

            "carrier": carrier_name or "Unavailable",

            "timezone": list(zones),

            "public_search": {
                "google": google_url,
                "bing": bing_url,
                "duckduckgo": duck_url
            },

            "note":
            "Public search results are not proof "
            "that a phone number belongs to a specific person."
        })

    except phonenumbers.NumberParseException as error:

        return jsonify({
            "ok": False,
            "error": f"Could not parse number: {error}"
        }), 400

    except Exception as error:

        return jsonify({
            "ok": False,
            "error": str(error)
        }), 500


# =========================================================
# IMAGE INSPECTION
# =========================================================

ALLOWED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tiff"
}


@app.route("/api/image", methods=["POST"])
def image_inspection():

    if "image" not in request.files:

        return jsonify({
            "ok": False,
            "error": "No image uploaded"
        }), 400

    uploaded = request.files["image"]

    if not uploaded.filename:

        return jsonify({
            "ok": False,
            "error": "No filename"
        }), 400

    extension = os.path.splitext(
        uploaded.filename
    )[1].lower()

    if extension not in ALLOWED_IMAGE_EXTENSIONS:

        return jsonify({
            "ok": False,
            "error": "Unsupported image format"
        }), 400

    filename = (
        uuid.uuid4().hex
        + extension
    )

    filepath = os.path.join(
        UPLOAD_DIR,
        filename
    )

    try:

        uploaded.save(filepath)

        sha256 = hashlib.sha256()

        with open(filepath, "rb") as file:

            while True:

                chunk = file.read(1024 * 1024)

                if not chunk:
                    break

                sha256.update(chunk)

        file_hash = sha256.hexdigest()

        with Image.open(filepath) as img:

            width, height = img.size

            image_format = img.format

            mode = img.mode

            exif_data = {}

            try:

                raw_exif = img.getexif()

                if raw_exif:

                    for key, value in raw_exif.items():

                        exif_data[str(key)] = str(value)

            except Exception:
                pass

        # External reverse-search pages.
        # Local upload নিজে থেকে Google/TinEye-তে পাঠানো হচ্ছে না।
        # User চাইলে browser-এ image upload করতে পারবে।

        reverse_search = {

            "Google Lens":
                "https://lens.google.com/",

            "Bing Visual Search":
                "https://www.bing.com/visualsearch",

            "TinEye":
                "https://tineye.com/",

            "Yandex Images":
                "https://yandex.com/images/"
        }

        return jsonify({

            "ok": True,

            "filename":
                uploaded.filename,

            "size_bytes":
                os.path.getsize(filepath),

            "sha256":
                file_hash,

            "width":
                width,

            "height":
                height,

            "format":
                image_format,

            "mode":
                mode,

            "exif":
                exif_data,

            "reverse_search":
                reverse_search,

            "note":
            "Reverse-image services may find public pages "
            "containing a visually similar image. "
            "They do not guarantee identification of a person "
            "or discovery of private accounts."
        })

    except Exception as error:

        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass

        return jsonify({
            "ok": False,
            "error": str(error)
        }), 400


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False
    )
