"""
One-shot script: geocode candidate locations and update location_lat/location_lng.
Deduplicates API calls — each unique location string is geocoded once.
"""
import asyncio
import re
import httpx
import asyncpg

DATABASE_URL = "postgresql://ja_management_portal:Lyra2332@34.93.50.115/ja_management_portal"
GMAPS_KEY = "AIzaSyAp3k877qDmqShRPsm7jGDjMcyCPNpqodo"

DATA = """Meghana Bhaskarrao\t8401071493\tMemnagar, Ahmedabad (2.3 KM)
Anjali Soni\t9978291120\tMemnagar, Ahmedabad (2.7 KM)
Jaimin Prajapati\t7984718932\tChanakyapuri, Ahmedabad (4.3 KM)
Zalak Dave\t8849176686\tScience City, Ahmedabad (3.9 KM)
Prinku Sharma\t6367461349\tPrahlad Nagar, Ahmedabad (4.2 KM)
Minesh Damor\t7573913086\tScience City, Ahmedabad (4.4 KM)
Nenshi Patel\t7016577302\tSola, Ahmedabad (2.6 KM)
Foram Rajeshbhai Pipaliya\t7621827614\tGhatlodia, Ahmedabad (3.7 KM)
Deep Jethava\t9558550062\tS G Highway, Ahmedabad (1.4 KM)
Shubhangi Arun Lonkar\t9325218668\tThaltej, Ahmedabad (0.0 KM)
Riddhi Patel\t9274233448\tChanakyapuri, Ahmedabad (4.3 KM)
Padhiyar Hemanshi\t9099678020\tRamdev Nagar, Ahmedabad (2.6 KM)
Rahul Chauhan\t7487885591\tSola Road, Ahmedabad (3.0 KM)
Kunj Pathak\t9909395650\tScience City, Ahmedabad (3.5 KM)
Avi Goswami\t9316335985\tChanakyapuri, Ahmedabad (4.0 KM)
Twinkal Chaniyara\t7046537272\tScience City, Ahmedabad (3.3 KM)
Sneha Kyada\t7984175428\tGurukul, Ahmedabad (1.8 KM)
Shah Virali\t9081033888\tNirnay Nagar, Ahmedabad (4.1 KM)
Ritu Vekariya\t9327747322\tBodakdev, Ahmedabad (1.5 KM)
Parmar Nandini Dipakbhai\t7863058140\tNirnay Nagar, Ahmedabad (4.8 KM)
Hetvi Hirapara\t9409968655\tSatellite, Ahmedabad (2.2 KM)
Ayush Vishwakarma\t8931869946\tGhatlodia, Ahmedabad (3.5 KM)
Vishva Shah\t7069864862\tSola Road, Ahmedabad (3.5 KM)
Nidhi Prajapati\t8401819021\tMemnagar, Ahmedabad (1.9 KM)
Sanjay Shah\t6353020430\tVasna, Ahmedabad (3.7 KM)
Rajput Tejansi\t8511417955\tJuhapura, Ahmedabad (1.8 KM)
Nilam Parmar\t8866484384\tSarkhej, Ahmedabad (3.2 KM)
Yamini Jadeja\t9327890998\tShela, Ahmedabad (4.5 KM)
Pratik Teli\t9001566886\tVishala, Ahmedabad (4.0 KM)
Pradip Doshi\t9428803911\tNarayan Nagar, Ahmedabad (4.5 KM)
Fazila Pathan\t9909763072\tSarkhej, Ahmedabad (3.5 KM)
Ashvad Shaikh\t9664793665\tSarkhej, Ahmedabad (4.1 KM)
Hafiz Vhora\t9016261597\tPrahlad Nagar, Ahmedabad (3.7 KM)
Kotak Bhumika\t9662771251\tAshok Vatika, Ahmedabad (2.7 KM)
Amisha\t9427118847\tC G Road, Ahmedabad (4.5 KM)
Brijesh Vedant\t9099707784\tNavrangpura, Ahmedabad (4.7 KM)
Lokesh Rawal\t8369051377\tNavrangpura, Ahmedabad (3.3 KM) | Preferred: Ahmedabad
Suthar Akash\t7575080692\tSola Road, Ahmedabad (3.3 KM)
Sankaliya Mahendra\t9574671846\tNavrangpura, Ahmedabad (4.3 KM)
Vidhi\t7435896265\tNavrangpura, Ahmedabad (4.5 KM)
Dolly Dubey\t6351385635\tC G Road, Ahmedabad (4.3 KM)
Meshwa Shah\t7984070322\tPaldi, Ahmedabad (4.9 KM)
Ankit\t7383940581\tC G Road, Ahmedabad (4.4 KM)
Jigneshgiri Gosai\t9924763346\tNavrangpura, Ahmedabad (4.6 KM)
Mohammad Uveish Mohammad Aiyub Modasiya\t9724278589\tNavrangpura, Ahmedabad (5.0 KM)
Savaniya Dhara Hemant Bhai\t9081336306\tC G Road, Ahmedabad (4.5 KM)
Avinash\t6375062167\tAyojan Nagar, Ahmedabad (3.9 KM)
Desai Janvi\t9054018270\tGurukul, Ahmedabad (4.1 KM)
Jenisha\t9909480102\tGurukul, Ahmedabad (4.1 KM)
Dimpal Patel\t7043432780\tSatellite, Ahmedabad (2.5 KM)
Patel Divya\t6353331804\tThaltej, Ahmedabad (4.4 KM)
Harsh Patel\t9408501884\tThaltej Road, Ahmedabad (4.2 KM)
Rakesh Parmar\t6353617188\tMakarba, Ahmedabad (1.9 KM)
Dabhi Jaydeep\t9157393483\tPaldi, Ahmedabad (4.6 KM)
Yamini Nirmal\t7041120083\tNava Wadaj, Ahmedabad (3.7 KM)
Bhavesh Sahsani\t7069493575\tNoblenagar, Ahmedabad (1.4 KM)
Chauhan Maitri\t9313452127\tDani Limbada, Ahmedabad (4.2 KM)
Khushali Shah\t9265675504\tRaipur, Ahmedabad (3.9 KM)
Adnan Ansari\t6351111279\tDani Limbada, Ahmedabad (4.9 KM)
Laxmi Vishwakarma\t9104918962\tLaxmanpura, Ahmedabad (1.8 KM)
Soni Roshni Gautam Bhai\t8511820050\tDudheshwar, Ahmedabad (3.0 KM)
Bheru Lal Rebari\t9784990712\tGita Mandir, Ahmedabad (4.1 KM)
Vatukiya Chatur\t9737544783\tGokuldham, Ahmedabad (3.5 KM)
Farhan Mansuri\t8141150317\tDariapur, Ahmedabad (3.2 KM)
Urvisha Rathod\t9328846370\tAshram Road, Ahmedabad (1.3 KM)
Khalas Krisha Ashok Bhai\t9574993486\tNavrangpura, Ahmedabad (0.8 KM)
Monali Soni\t8140912905\tLaxmanpura, Ahmedabad (2.0 KM)
Akshay Kumar Teli\t7023115940\tNavrangpura, Ahmedabad (0.1 KM)
Het Bhavsar\t9714974050\tKankaria, Ahmedabad (4.9 KM)
Amit Verma\t8299604424\tPaldi, Ahmedabad (2.2 KM)
Agam Shah\t9714178362\tNavjivan, Ahmedabad (1.2 KM)
Sandip J Makwana\t8980484938\tSardar Colony, Ahmedabad (2.7 KM)
Vishal Chavda\t6351246886\tShahpur, Ahmedabad (2.0 KM)
Hitesha Bhavsar\t7228991634\tKankaria, Ahmedabad (4.9 KM)
Keval Modi\t9978114188\tRaipur, Ahmedabad (3.9 KM)
Abdul Ahad\t8866553604\tKalupur, Ahmedabad (3.6 KM)
Urvashi Kamani\t9327230371\tPaldi, Ahmedabad (2.6 KM)
Ankit Pandya\t9725436705\tAshram Road, Ahmedabad (1.3 KM)
Rathod Maitri Dineshbhai\t9638439659\tLaxmanpura, Ahmedabad (1.9 KM)
Priyanka Rathod\t9737693455\tNava Wadaj, Ahmedabad (3.1 KM)
Yadav Rahul\t9316613453\tMadhupura, Ahmedabad (2.6 KM)
Dhara Pandit\t9274592882\tSola Road, Ahmedabad (4.2 KM)
Parmar Yagni Vinodbha\t7600904370\tGirdhar Nagar, Ahmedabad (4.2 KM)
Jigar Jani\t9409573534\tDudheshwar, Ahmedabad (2.7 KM)
Jaymin Patni\t7990176324\tJuna Wadaj, Ahmedabad (2.4 KM)
Aanchal Tripathi\t8460070520\tLaxmanpura, Ahmedabad (1.3 KM)
Hitarth Shah\t9510540387\tNava Wadaj, Ahmedabad (3.6 KM)
Sahdevsinh Mamera\t9313695722\tKhanpur, Ahmedabad (1.2 KM)
Zala Pushparajsinh J\t9727392490\tShyamal, Ahmedabad (3.9 KM)
Mahendar Prajapati\t7016358819\tRaipur, Ahmedabad (3.7 KM)
Kirtan Lodha\t9316759030\tKhanpur, Ahmedabad (1.2 KM)
Tapan Kadiya\t9924676048\tAshok Vatika, Ahmedabad (3.2 KM)
Shreya Raval\t9099332920\tBopal, Ahmedabad (1.5 KM)
Mayur Gohel\t8733851485\tThaltej, Ahmedabad (4.5 KM)
Anjali Shukla\t7778892030\tGhuma, Ahmedabad (2.2 KM)
Shaikh Saif Ali Ashraf Ali\t9054992050\tJuhapura, Ahmedabad (2.3 KM)
Priya Panchal\t9727778920\tVishala, Ahmedabad (5.0 KM)
Akshay Kamalkumar Bachani\t8758487775\tSola, Ahmedabad (4.0 KM)
Anjali\t7600239200\tC G Road, Ahmedabad (4.4 KM)
Pooja Krishnani\t6353377390\tS G Highway, Ahmedabad (2.4 KM)
Krisha Palan\t6355957979\tC G Road, Ahmedabad (3.7 KM)
Suthar Shivani Tulsibhai\t9601738721\tNew Ranip, Ahmedabad (4.9 KM)
Jay Prajapati\t8850380043\tChanakyapuri, Ahmedabad (3.0 KM)
Chirag Bhilocha\t8401791569\tNava Wadaj, Ahmedabad (3.8 KM)
Dhara\t9904584622\tRanip, Ahmedabad (5.0 KM)
Manish Khanvilkar\t9327226495\tAshram Road, Ahmedabad (3.8 KM)
Vishal Ravat\t9510185563\tNava Wadaj, Ahmedabad (4.2 KM)
Dipti Jayeshbhai Khakhkhar\t7016315316\tAnandnagar, Ahmedabad (4.4 KM)
Sankliya Bhautik\t9624703201\tNirnay Nagar, Ahmedabad (3.5 KM)
Sanjay Talreja\t8140417593\tEllis Bridge, Ahmedabad (4.2 KM)
Kanal Prajapati\t9099739046\tAnandnagar, Ahmedabad (4.0 KM)
Surbhi Gond\t9023102962\tKhanpur, Ahmedabad (4.5 KM)
Yash Solanki\t7046646670\tAshram Road, Ahmedabad (4.1 KM)
Sarthak Goswami\t7265827205\tRanip, Ahmedabad (5.0 KM)
Suraj Yadav\t9825846248\tChandlodia, Ahmedabad (4.3 KM)
Jitendra Kumar\t8485958500\tAnandnagar, Ahmedabad (4.1 KM)
Sunil Prajapati\t7436062948\tSabarmati, Ahmedabad (4.3 KM)
Dipesh Shah\t9328100797\tPaldi, Ahmedabad (3.2 KM)
Chauhan Sejal\t9328666296\tBehrampura, Ahmedabad (4.7 KM)
Chauhan Sonal\t7041504096\tSabarmati, Ahmedabad (4.3 KM)
Akash Patel\t8160190812\tNarol, Ahmedabad (4.7 KM)
Patil Vidhi Sunil Bhai\t9016537279\tManinagar, Ahmedabad (4.3 KM)
Aman Shaikh\t8511203672\tGomtipur, Ahmedabad (4.7 KM)
Janvi Solanki\t9274085346\tShah E Alam Roja, Ahmedabad (3.8 KM)
Meet Shah\t8490933440\tManinagar, Ahmedabad (4.7 KM)
Bharat Prajapati\t8690209033\tSarangpur, Ahmedabad (3.7 KM)
Megha\t8320331391\tManinagar, Ahmedabad (4.3 KM)
Bhargav Prajapati\t6378377146\tRaipur, Ahmedabad (3.1 KM)
Pathan Sahad Khan\t7600009862\tDudheshwar, Ahmedabad (4.3 KM)
Nisha Shah\t9106320361\tIsanpur, Ahmedabad (4.7 KM)
Jadav Shweta Dipak Bhai\t9974015212\tKalupur, Ahmedabad (4.0 KM)
Vishal Ramanikbhai Maru\t9099024319\tCharel, Ahmedabad
Yash Patel\t8140005157\tDudheshwar, Ahmedabad (1.9 KM)
Pankaj Babubhai Prajapati\t8128860343\tSaraspur, Ahmedabad (4.7 KM)
Komal Solanki\t8735951584\tSaraspur, Ahmedabad (4.9 KM)
Gautam Aemul\t8128853254\tAsarwa, Ahmedabad (4.4 KM)
Gautam Natvarbhai Vanzara\t6359349483\tAsarwa, Ahmedabad (4.2 KM)
Harshida Suthar\t7433026284\tRanip, Ahmedabad (4.5 KM)
Mamta Parmarmamtagmailcom\t9586619594\tSarangpur, Ahmedabad (4.1 KM)
Saloni Mansuri\t9974357049\tShahpur, Ahmedabad (1.3 KM)
Karan Parmar\t8401382156\tGirdhar Nagar, Ahmedabad (3.3 KM) | Preferred: Ahmedabad
Pujashri Mallikarjun Bandgar\t6353486040\tBehrampura, Ahmedabad (4.5 KM)
Mitali Bhavsar\t9904473146\tD Colony, Ahmedabad (4.6 KM)
Parmar Neel\t8866071056\tSarangpur, Ahmedabad (4.5 KM)
Jaydip Rao\t9313681249\tSarangpur, Ahmedabad (4.4 KM)
Kinjal Patel\t8849444134\tDudheshwar, Ahmedabad (1.7 KM)
Qureshi Vashil\t7383327274\tDudheshwar, Ahmedabad (1.8 KM)
Vishal Parmar\t9904820560\tEllis Bridge, Ahmedabad (1.7 KM)
Dev Kuchara\t9265627382\tSaraspur, Ahmedabad (3.8 KM)
Badal Nadiya\t7600978072\tAsarwa, Ahmedabad (4.1 KM)
Purva Bhavsar\t6354891827\tNava Wadaj, Ahmedabad (2.7 KM)
Thakor Jitendra\t8780029612\tMadhupura, Ahmedabad (2.0 KM)
Aman Manihar\t6355714898\tSaraspur, Ahmedabad (4.0 KM)
Nikhil Bipinkumar Panchal\t7227859625\tAnandnagar, Ahmedabad (4.2 KM)
Rakesh Parmar\t8849014358\tBehrampura, Ahmedabad (4.6 KM)
Parma Nayana\t7984330539\tKankaria, Ahmedabad (4.5 KM)"""


def parse_location(raw: str) -> str:
    """Strip '(X.X KM)' suffix and '| Preferred: ...' annotation."""
    loc = raw.strip()
    loc = re.sub(r'\s*\|\s*Preferred:.*$', '', loc)
    loc = re.sub(r'\s*\(\d+\.?\d*\s*KM\)\s*$', '', loc, flags=re.IGNORECASE)
    return loc.strip()


def parse_data() -> list[dict]:
    rows = []
    for line in DATA.strip().splitlines():
        parts = line.split('\t')
        if len(parts) < 3:
            continue
        name = parts[0].strip()
        phone = parts[1].strip()
        location = parse_location(parts[2])
        rows.append({"name": name, "phone": phone, "location": location})
    return rows


async def geocode(location: str, http: httpx.AsyncClient) -> tuple[float | None, float | None]:
    try:
        r = await http.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": location, "key": GMAPS_KEY},
        )
        results = r.json().get("results", [])
        if results:
            loc = results[0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
    except Exception as e:
        print(f"  [geocode error] {location}: {e}")
    return None, None


async def main():
    rows = parse_data()
    print(f"Parsed {len(rows)} candidates")

    # Deduplicate locations
    unique_locations = list({r["location"] for r in rows})
    print(f"Unique locations to geocode: {len(unique_locations)}")

    geo_cache: dict[str, tuple[float | None, float | None]] = {}

    async with httpx.AsyncClient(timeout=10.0) as http:
        for loc in unique_locations:
            lat, lng = await geocode(loc, http)
            geo_cache[loc] = (lat, lng)
            status = f"{lat:.4f}, {lng:.4f}" if lat else "FAILED"
            print(f"  {loc} → {status}")

    # Connect to DB and upsert
    conn = await asyncpg.connect(DATABASE_URL)
    inserted = 0
    coord_updated = 0
    geo_failed = 0

    try:
        for r in rows:
            lat, lng = geo_cache.get(r["location"], (None, None))
            if lat is None:
                print(f"  [skip geocode failed] {r['name']} ({r['phone']})")
                geo_failed += 1
                continue

            row = await conn.fetchrow(
                """
                INSERT INTO candidates (
                    name, phone, current_location, location_lat, location_lng,
                    source, is_active, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, 'sourced', true, now(), now())
                ON CONFLICT (phone) DO UPDATE
                    SET location_lat     = EXCLUDED.location_lat,
                        location_lng     = EXCLUDED.location_lng,
                        current_location = EXCLUDED.current_location,
                        updated_at       = now()
                RETURNING (xmax = 0) AS was_inserted
                """,
                r["name"], r["phone"], r["location"], lat, lng,
            )
            if row["was_inserted"]:
                inserted += 1
            else:
                coord_updated += 1
    finally:
        await conn.close()

    print(f"\nDone: {inserted} inserted | {coord_updated} coords updated on existing | {geo_failed} geocode failures")


if __name__ == "__main__":
    asyncio.run(main())
