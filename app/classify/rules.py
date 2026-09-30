"""Keyword rules for turning a parsed row into MEANING (bucket + category).

This file is data, not logic, so ops can extend it without touching the classifier.
Rules of thumb:
  * Keywords are matched as whole words (so BET never matches "HIRAM KABATA BETH").
  * When unsure, lean conservative: an unknown outflow is treated as a NEED by the
    assessment, which shrinks what the customer is "left with", never inflates it.
  * Keep lists UPPERCASE.
"""

# Buckets, and what the assessment does with each:
#   INCOME         money that genuinely came in (salary, business, people, cash deposits)
#   SELF_TRANSFER  customer moving their own money (bank <-> M-PESA, M-Shwari savings)
#   REFUND         money coming back (reversals, merchant refunds) - offsets spend
#   BORROWED       loan money in (Fuliza, M-Shwari/KCB loans, loan apps) - NEVER income
#   NEED           spend they'd make regardless (food, water, power, transport, airtime)
#   COMMITTED      obligations (rent, school, loans, bank, money sent to people)
#   WANT           discretionary (food delivery, bars, betting, subscriptions)
#   FEE            M-PESA charges
#   UNKNOWN        we couldn't tell; assessment treats outflows as NEED

BANK_WORDS = [
    "BANK", "EQUITY", "KCB", "CO-OPERATIVE", "COOPERATIVE", "NCBA", "ABSA", "STANBIC",
    "I&M", "IM BANK", "DTB", "DIAMOND TRUST", "FAMILY BANK", "SIDIAN", "STANDARD CHARTERED",
    "NATIONAL BANK", "CONSOLIDATED", "GULF AFRICAN", "HFC", "KINGDOM BANK", "POSTBANK",
]

PAYMENT_GATEWAY_WORDS = ["CELLULANT", "TINGG", "PESAPAL", "IPAY", "DPO", "FLUTTERWAVE", "JAMBOPAY"]

LOAN_APP_WORDS = [
    "TALA", "BRANCH", "ZENKA", "OKASH", "OPESA", "HUSTLER FUND", "HUSTLERS FUND", "ZASH",
    "IPESA", "SOCIAL LENDING", "BERRY", "KASHWAY", "TIMIZA", "EAZZY LOAN", "FULIZA",
    "M-SHWARI", "MSHWARI", "KCB M-PESA", "LIPA LATER", "AZURA", "MOGO", "WATU", "M-KOPA", "MKOPA",
]
# Device / asset financing: a direct competitor signal for CelliPay.
ASSET_FINANCE_WORDS = ["M-KOPA", "MKOPA", "WATU", "MOGO", "LIPA LATER", "AZURA", "SUN KING", "SUNKING", "D.LIGHT"]

BETTING_WORDS = [
    "SPORTPESA", "BETIKA", "ODIBETS", "ODIBET", "MOZZARTBET", "MOZZART", "BETIN", "SHABIKI",
    "1XBET", "22BET", "BETWAY", "KWIKBET", "BANGBET", "HELABET", "SPORTYBET", "BETLION",
    "PLAYMASTER", "LUCKY 2U", "CHEZACASH", "BETSAFE", "GAL SPORT", "GALSPORT",
]

# (category, bucket, keywords). First match wins, so specific before generic.
MERCHANT_RULES: list[tuple[str, str, list[str]]] = [
    ("BETTING", "WANT", BETTING_WORDS),
    ("ASSET_FINANCE_REPAYMENT", "COMMITTED", ASSET_FINANCE_WORDS),
    ("LOAN_REPAYMENT", "COMMITTED", LOAN_APP_WORDS),
    ("FOOD_DELIVERY", "WANT", ["GLOVO", "UBER EATS", "BOLT FOOD", "JUMIA FOOD", "YUMMY"]),
    ("TV_SUBSCRIPTION", "WANT", ["DSTV", "GOTV", "STARTIMES", "ZUKU TV", "SHOWMAX"]),
    ("DIGITAL_SUBSCRIPTIONS", "WANT", ["NETFLIX", "SPOTIFY", "APPLE", "GOOGLE", "YOUTUBE",
                                       "ANTHROPIC", "OPENAI", "CHATGPT", "MICROSOFT", "AMAZON PRIME",
                                       "CANVA", "ADOBE", "BOOMPLAY", "AUDIOMACK"]),
    ("BARS_ALCOHOL", "WANT", ["TAVERN", "BAR", "PUB", "LOUNGE", "WINES", "SPIRITS", "LIQUOR", "CLUB"]),
    ("EATING_OUT", "WANT", ["RESTAURANT", "CAFE", "JAVA", "KFC", "PIZZA", "CHICKEN INN", "EATERY",
                            "ARTCAFFE", "BURGER", "GRILL", "CHOMA"]),
    ("ELECTRICITY", "NEED", ["KPLC", "KENYA POWER", "PREPAID TOKENS"]),
    ("INTERNET", "NEED", ["ZUKU", "FAIBA", "SAFARICOM HOME", "HOME FIBRE", "FIBER", "FIBRE",
                          "NETWORKS", "NETPOINT", "WIFI", "INTERNET", "POA INTERNET"]),
    ("WATER", "NEED", ["NAIROBI WATER", "NCWSC", "WATER COMPANY", "WATERS", "WATER"]),
    ("RENT", "COMMITTED", ["RENT", "APARTMENTS", "PROPERTIES", "PROPERTY", "ESTATE AGENTS", "LANDLORD"]),
    ("SCHOOL_FEES", "COMMITTED", ["SCHOOL", "ACADEMY", "UNIVERSITY", "COLLEGE", "HELB"]),
    ("INSURANCE_STATUTORY", "COMMITTED", ["NHIF", "SHA", "SOCIAL HEALTH", "NSSF", "INSURANCE", "JUBILEE", "BRITAM", "CIC"]),
    ("TAX", "COMMITTED", ["KRA", "KENYA REVENUE"]),
    ("SACCO_CHAMA", "COMMITTED", ["SACCO", "CHAMA", "WELFARE"]),
    ("BANK_TRANSFER", "COMMITTED", BANK_WORDS),
    ("HEALTH", "NEED", ["PHARMACEUTICAL", "PHARMACY", "PHARMA", "CHEMIST", "HOSPITAL", "CLINIC",
                        "MEDICAL", "DENTAL", "LAB"]),
    ("TRANSPORT_FUEL", "NEED", ["TOTALENERGIES", "TOTAL", "SHELL", "RUBIS", "OLA ENERGY", "PETROL",
                                "FUEL", "SERVICE STATION", "UBER", "BOLT", "LITTLE CAB", "SACCO MATATU"]),
    ("GROCERIES_HOUSEHOLD", "NEED", ["NAIVAS", "QUICKMART", "QUICK MART", "CARREFOUR", "CHANDARANA",
                                     "CLEANSHELF", "EASTMATT", "MULLEYS", "MAJID", "MINI MART", "MINIMART",
                                     "SUPERMARKET", "SUPERMARKETS", "STORES", "KIOSK", "DUKA", "BUTCHERY",
                                     "GROCERS", "GROCERY", "MART", "GAS", "HARDWARE", "BAKERY"]),
]

# Business payers (B2C "Business Payment from X") by what they tell us.
B2C_LOAN_DISBURSERS = LOAN_APP_WORDS
B2C_BETTING = BETTING_WORDS