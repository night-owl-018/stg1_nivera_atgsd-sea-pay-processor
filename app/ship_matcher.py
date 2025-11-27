import re
from rapidfuzz import fuzz, process

VALID_SHIPS = [
    "USS America", "USS Anchorage", "USS Arleigh Burke", "USS Arlington",
    "USS Ashland", "USS Augusta", "USS Bainbridge", "USS Barry", "USS Bataan",
    "USS Beloit", "USS Benfold", "USS Billings", "USS Blue Ridge", "USS Boxer",
    "USS Bulkeley", "USS Canberra", "USS Cape St. George", "USS Carl M. Levin",
    "USS Carney", "USS Carter Hall", "USS Chafee", "USS Charleston",
    "USS Chief", "USS Chosin", "USS Chung-Hoon", "USS Cincinnati",
    "USS Cole", "USS Comstock", "USS Cooperstown", "USS Curtis Wilbur",
    "USS Daniel Inouye", "USS Decatur", "USS Delbert D. Black", "USS Dewey",
    "USS Donald Cook", "USS Essex", "USS Farragut", "USS Fitzgerald",
    "USS Forrest Sherman", "USS Fort Lauderdale", "USS Fort Worth",
    "USS Frank E. Petersen Jr.", "USS Gabrielle Giffords", "USS Germantown",
    "USS Gettysburg", "USS Gonzalez", "USS Gravely", "USS Green Bay",
    "USS Gridley", "USS Gunston Hall", "USS Halsey", "USS Harpers Ferry",
    "USS Higgins", "USS Hopper", "USS Howard", "USS Indianapolis", "USS Iwo Jima",
    "USS Jackson", "USS Jack H. Lucas", "USS James E. Williams",
    "USS Jason Dunham", "USS John Basilone", "USS John Finn",
    "USS John P. Murtha", "USS John Paul Jones", "USS John S. McCain",
    "USS Kansas City", "USS Kearsarge", "USS Kidd", "USS Kingsville",
    "USS Laboon", "USS Lake Erie", "USS Lassen", "USS Lenah Sutcliffe Higbee",
    "USS Mahan", "USS Makin Island", "USS Manchester", "USS Marinette",
    "USS Mason", "USS McCampbell", "USS McFaul", "USS Mesa Verde",
    "USS Michael Monsoor", "USS Michael Murphy", "USS Milius",
    "USS Minneapolis–Saint Paul", "USS Mitscher", "USS Mobile", "USS Momsen",
    "USS Montgomery", "USS Mount Whitney", "USS Mustin", "USS Nantucket",
    "USS New Orleans", "USS New York", "USS Nitze", "USS O’Kane",
    "USS Oak Hill", "USS Oakland", "USS Omaha", "USS Oscar Austin",
    "USS Patriot", "USS Paul Hamilton", "USS Paul Ignatius",
    "USS Pearl Harbor", "USS Pinckney", "USS Pioneer", "USS Porter",
    "USS Portland", "USS Preble", "USS Princeton", "USS Rafael Peralta",
    "USS Ralph Johnson", "USS Ramage", "USS Richard M. McCool Jr.",
    "USS Robert Smalls", "USS Roosevelt", "USS Ross", "USS Rushmore",
    "USS Russell", "USS Sampson", "USS San Antonio", "USS San Diego",
    "USS Santa Barbara", "USS Savannah", "USS Shiloh", "USS Shoup",
    "USS Somerset", "USS Spruance", "USS St. Louis", "USS Sterett",
    "USS Stethem", "USS Stockdale", "USS Stout", "USS The Sullivans",
    "USS Tortuga", "USS Tripoli", "USS Truxtun", "USS Tulsa", "USS Warrior",
    "USS Wasp", "USS Wayne E. Meyer", "USS William P. Lawrence",
    "USS Winston S. Churchill", "USS Wichita", "USS Zumwalt"
]


def match_ship(raw):
    """Return official ship name from messy extracted string."""
    if not raw:
        return ""

    cleaned = raw.upper()
    cleaned = re.sub(r"\(.*?\)", " ", cleaned)      # remove "(ASW C)"
    cleaned = re.sub(r"[^A-Z\s]", " ", cleaned)     # remove numbers/symbols
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    match, score, _ = process.extractOne(
        cleaned,
        VALID_SHIPS,
        scorer=fuzz.token_sort_ratio
    )

    if score < 60:
        return cleaned  # fallback

    return match

