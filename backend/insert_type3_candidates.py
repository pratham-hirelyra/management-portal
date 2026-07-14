"""
Insert 156 Type-3 DND candidates with geocoded locations.
source='sourced', is_active=False, dnd_until='2026-10-03'
"""
import asyncio, os, re, httpx, asyncpg
from dotenv import load_dotenv

load_dotenv()

RAW_DATA = """Meghana Bhaskarrao\t8401071493\tMemnagar, Ahmedabad (2.3 KM)\t
Anjali Soni\t9978291120\tMemnagar, Ahmedabad (2.7 KM)\t
Jaimin Prajapati\t7984718932\tChanakyapuri, Ahmedabad (4.3 KM)\t
Zalak Dave\t8849176686\tScience City, Ahmedabad (3.9 KM)\t
Prinku Sharma\t6367461349\tPrahlad Nagar, Ahmedabad (4.2 KM)\t
Minesh Damor\t7573913086\tScience City, Ahmedabad (4.4 KM)\t
Nenshi Patel\t7016577302\tSola, Ahmedabad (2.6 KM)\t
Foram Rajeshbhai Pipaliya\t7621827614\tGhatlodia, Ahmedabad (3.7 KM)\t30551894
Deep Jethava\t9558550062\tS G Highway, Ahmedabad (1.4 KM)\t14028352
Shubhangi Arun Lonkar\t9325218668\tThaltej, Ahmedabad (0.0 KM)\t42251756
Riddhi Patel\t9274233448\tChanakyapuri, Ahmedabad (4.3 KM)\t41050411
Padhiyar Hemanshi\t9099678020\tRamdev Nagar, Ahmedabad (2.6 KM)\t33558586
Rahul Chauhan\t7487885591\tSola Road, Ahmedabad (3.0 KM)\t44897098
Kunj Pathak\t9909395650\tScience City, Ahmedabad (3.5 KM)\t39481698
Avi Goswami\t9316335985\tChanakyapuri, Ahmedabad (4.0 KM)\t38220520
Twinkal Chaniyara\t7046537272\tScience City, Ahmedabad (3.3 KM)\t19541287
Sneha Kyada\t7984175428\tGurukul, Ahmedabad (1.8 KM)\t39970172
Shah Virali\t9081033888\tNirnay Nagar, Ahmedabad (4.1 KM)\t18210013
Ritu Vekariya\t9327747322\tBodakdev, Ahmedabad (1.5 KM)\t36758303
Parmar Nandini Dipakbhai\t7863058140\tNirnay Nagar, Ahmedabad (4.8 KM)\t25064858
Hetvi Hirapara\t9409968655\tSatellite, Ahmedabad (2.2 KM)\t35550064
Ayush Vishwakarma\t8931869946\tGhatlodia, Ahmedabad (3.5 KM)\t
Vishva Shah\t7069864862\tSola Road, Ahmedabad (3.5 KM)\t44023903
Nidhi Prajapati\t8401819021\tMemnagar, Ahmedabad (1.9 KM)\t36413213
Sanjay Shah\t6353020430\tVasna, Ahmedabad (3.7 KM)\t42545226
Rajput Tejansi\t8511417955\tJuhapura, Ahmedabad (1.8 KM)\t34446222
Nilam Parmar\t8866484384\tSarkhej, Ahmedabad (3.2 KM)\t46380989
Yamini Jadeja\t9327890998\tShela, Ahmedabad (4.5 KM)\t
Pratik Teli\t9001566886\tVishala, Ahmedabad (4.0 KM)\t46326912
Pradip Doshi\t9428803911\tNarayan Nagar, Ahmedabad (4.5 KM)\t40467499
Fazila Pathan\t9909763072\tSarkhej, Ahmedabad (3.5 KM)\t42753013
Ashvad Shaikh\t9664793665\tSarkhej, Ahmedabad (4.1 KM)\t30404824
Hafiz Vhora\t9016261597\tPrahlad Nagar, Ahmedabad (3.7 KM)\t46040762
Kotak Bhumika\t9662771251\tAshok Vatika, Ahmedabad (2.7 KM)\t44039606
Amisha\t9427118847\tC G Road, Ahmedabad (4.5 KM)\t24294863
Brijesh Vedant\t9099707784\tNavrangpura, Ahmedabad (4.7 KM)\t40772038
Lokesh Rawal\t8369051377\tNavrangpura, Ahmedabad (3.3 KM) | Preferred: Ahmedabad\t35778345
Suthar Akash\t7575080692\tSola Road, Ahmedabad (3.3 KM)\t43692263
Sankaliya Mahendra\t9574671846\tNavrangpura, Ahmedabad (4.3 KM)\t37868237
Vidhi\t7435896265\tNavrangpura, Ahmedabad (4.5 KM)\t13010191
Dolly Dubey\t6351385635\tC G Road, Ahmedabad (4.3 KM)\t22114011
Meshwa Shah\t7984070322\tPaldi, Ahmedabad (4.9 KM)\t31248817
Ankit\t7383940581\tC G Road, Ahmedabad (4.4 KM)\t37780006
Jigneshgiri Gosai\t9924763346\tNavrangpura, Ahmedabad (4.6 KM)\t12121308
Mohammad Uveish Mohammad Aiyub Modasiya\t9724278589\tNavrangpura, Ahmedabad (5.0 KM)\t43019884
Savaniya Dhara Hemant Bhai\t9081336306\tC G Road, Ahmedabad (4.5 KM)\t40351600
Avinash\t6375062167\tAyojan Nagar, Ahmedabad (3.9 KM)\t35807377
Desai Janvi\t9054018270\tGurukul, Ahmedabad (4.1 KM)\t46540746
Jenisha\t9909480102\tGurukul, Ahmedabad (4.1 KM)\t26235750
Dimpal Patel\t7043432780\tSatellite, Ahmedabad (2.5 KM)\t46558963
Patel Divya\t6353331804\tThaltej, Ahmedabad (4.4 KM)\t41043537
Harsh Patel\t9408501884\tThaltej Road, Ahmedabad (4.2 KM)\t7872842
Rakesh Parmar\t6353617188\tMakarba, Ahmedabad (1.9 KM)\t31138070
Dabhi Jaydeep\t9157393483\tPaldi, Ahmedabad (4.6 KM)\t32886390
Yamini Nirmal\t7041120083\tNava Wadaj, Ahmedabad (3.7 KM)\t43703660
Bhavesh Sahsani\t7069493575\tNoblenagar, Ahmedabad (1.4 KM)\t25054482
Chauhan Maitri\t9313452127\tDani Limbada, Ahmedabad (4.2 KM)\t33861241
Khushali Shah\t9265675504\tRaipur, Ahmedabad (3.9 KM)\t39048190
Adnan Ansari\t6351111279\tDani Limbada, Ahmedabad (4.9 KM)\t22553900
Laxmi Vishwakarma\t9104918962\tLaxmanpura, Ahmedabad (1.8 KM)\t32027217
Soni Roshni Gautam Bhai\t8511820050\tDudheshwar, Ahmedabad (3.0 KM)\t26858578
Bheru Lal Rebari\t9784990712\tGita Mandir, Ahmedabad (4.1 KM)\t31118891
Vatukiya Chatur\t9737544783\tGokuldham, Ahmedabad (3.5 KM)\t17248591
Farhan Mansuri\t8141150317\tDariapur, Ahmedabad (3.2 KM)\t44154831
Urvisha Rathod\t9328846370\tAshram Road, Ahmedabad (1.3 KM)\t40195723
Khalas Krisha Ashok Bhai\t9574993486\tNavrangpura, Ahmedabad (0.8 KM)\t35702416
Monali Soni\t8140912905\tLaxmanpura, Ahmedabad (2.0 KM)\t35997812
Akshay Kumar Teli\t7023115940\tNavrangpura, Ahmedabad (0.1 KM)\t15523244
Het Bhavsar\t9714974050\tKankaria, Ahmedabad (4.9 KM)\t25846986
Amit Verma\t8299604424\tPaldi, Ahmedabad (2.2 KM)\t36673220
Agam Shah\t9714178362\tNavjivan, Ahmedabad (1.2 KM)\t39659038
Sandip J Makwana\t8980484938\tSardar Colony, Ahmedabad (2.7 KM)\t21700249
Vishal Chavda\t6351246886\tShahpur, Ahmedabad (2.0 KM)\t39020514
Hitesha Bhavsar\t7228991634\tKankaria, Ahmedabad (4.9 KM)\t16190988
Keval Modi\t9978114188\tRaipur, Ahmedabad (3.9 KM)\t39810613
Abdul Ahad\t8866553604\tKalupur, Ahmedabad (3.6 KM)\t32798458
Urvashi Kamani\t9327230371\tPaldi, Ahmedabad (2.6 KM)\t31865871
Ankit Pandya\t9725436705\tAshram Road, Ahmedabad (1.3 KM)\t23528214
Rathod Maitri Dineshbhai\t9638439659\tLaxmanpura, Ahmedabad (1.9 KM)\t39132544
Priyanka Rathod\t9737693455\tNava Wadaj, Ahmedabad (3.1 KM)\t30542905
Yadav Rahul\t9316613453\tMadhupura, Ahmedabad (2.6 KM)\t18468043
Dhara Pandit\t9274592882\tSola Road, Ahmedabad (4.2 KM)\t23254485
Parmar Yagni Vinodbha\t7600904370\tGirdhar Nagar, Ahmedabad (4.2 KM)\t31044679
Jigar Jani\t9409573534\tDudheshwar, Ahmedabad (2.7 KM)\t3792999
Jaymin Patni\t7990176324\tJuna Wadaj, Ahmedabad (2.4 KM)\t39063662
Aanchal Tripathi\t8460070520\tLaxmanpura, Ahmedabad (1.3 KM)\t21125990
Hitarth Shah\t9510540387\tNava Wadaj, Ahmedabad (3.6 KM)\t27981477
Sahdevsinh Mamera\t9313695722\tKhanpur, Ahmedabad (1.2 KM)\t42501028
Zala Pushparajsinh J\t9727392490\tShyamal, Ahmedabad (3.9 KM)\t33959889
Mahendar Prajapati\t7016358819\tRaipur, Ahmedabad (3.7 KM)\t31234230
Kirtan Lodha\t9316759030\tKhanpur, Ahmedabad (1.2 KM)\t25101654
Tapan Kadiya\t9924676048\tAshok Vatika, Ahmedabad (3.2 KM)\t35109707
Shreya Raval\t9099332920\tBopal, Ahmedabad (1.5 KM)\t17880667
Mayur Gohel\t8733851485\tThaltej, Ahmedabad (4.5 KM)\t16775664
Anjali Shukla\t7778892030\tGhuma, Ahmedabad (2.2 KM)\t44952086
Shaikh Saif Ali Ashraf Ali\t9054992050\tJuhapura, Ahmedabad (2.3 KM)\t38161528
Priya Panchal\t9727778920\tVishala, Ahmedabad (5.0 KM)\t23568785
Akshay Kamalkumar Bachani\t8758487775\tSola, Ahmedabad (4.0 KM)\t12111059
Anjali\t7600239200\tC G Road, Ahmedabad (4.4 KM)\t17394276
Pooja Krishnani\t6353377390\tS G Highway, Ahmedabad (2.4 KM)\t45619663
Krisha Palan\t6355957979\tC G Road, Ahmedabad (3.7 KM)\t37942148
Suthar Shivani Tulsibhai\t9601738721\tNew Ranip, Ahmedabad (4.9 KM)\t3818119
Jay Prajapati\t8850380043\tChanakyapuri, Ahmedabad (3.0 KM)\t41728598
Chirag Bhilocha\t8401791569\tNava Wadaj, Ahmedabad (3.8 KM)\t44921574
Dhara\t9904584622\tRanip, Ahmedabad (5.0 KM)\t45163993
Manish Khanvilkar\t9327226495\tAshram Road, Ahmedabad (3.8 KM)\t37637750
Vishal Ravat\t9510185563\tNava Wadaj, Ahmedabad (4.2 KM)\t20266580
Dipti Jayeshbhai Khakhkhar\t7016315316\tAnandnagar, Ahmedabad (4.4 KM)\t41160957
Sankliya Bhautik\t9624703201\tNirnay Nagar, Ahmedabad (3.5 KM)\t39414876
Sanjay Talreja\t8140417593\tEllis Bridge, Ahmedabad (4.2 KM)\t5869159
Kanal Prajapati\t9099739046\tAnandnagar, Ahmedabad (4.0 KM)\t13540552
Surbhi Gond\t9023102962\tKhanpur, Ahmedabad (4.5 KM)\t44745257
Yash Solanki\t7046646670\tAshram Road, Ahmedabad (4.1 KM)\t41467724
Sarthak Goswami\t7265827205\tRanip, Ahmedabad (5.0 KM)\t138576
Suraj Yadav\t9825846248\tChandlodia, Ahmedabad (4.3 KM)\t31580389
Jitendra Kumar\t8485958500\tAnandnagar, Ahmedabad (4.1 KM)\t14099742
Sunil Prajapati\t7436062948\tSabarmati, Ahmedabad (4.3 KM)\t7365338
Dipesh Shah\t9328100797\tPaldi, Ahmedabad (3.2 KM)\t42851567
Chauhan Sejal\t9328666296\tBehrampura, Ahmedabad (4.7 KM)\t20893480
Chauhan Sonal\t7041504096\tSabarmati, Ahmedabad (4.3 KM)\t11852264
Akash Patel\t8160190812\tNarol, Ahmedabad (4.7 KM)\t32920678
Patil Vidhi Sunil Bhai\t9016537279\tManinagar, Ahmedabad (4.3 KM)\t41678309
Aman Shaikh\t8511203672\tGomtipur, Ahmedabad (4.7 KM)\t30873155
Janvi Solanki\t9274085346\tShah E Alam Roja, Ahmedabad (3.8 KM)\t41321779
Meet Shah\t8490933440\tManinagar, Ahmedabad (4.7 KM)\t46718895
Bharat Prajapati\t8690209033\tSarangpur, Ahmedabad (3.7 KM)\t3704823
Megha\t8320331391\tManinagar, Ahmedabad (4.3 KM)\t46046479
Bhargav Prajapati\t6378377146\tRaipur, Ahmedabad (3.1 KM)\t45702868
Pathan Sahad Khan\t7600009862\tDudheshwar, Ahmedabad (4.3 KM)\t38473034
Nisha Shah\t9106320361\tIsanpur, Ahmedabad (4.7 KM)\t34526498
Jadav Shweta Dipak Bhai\t9974015212\tKalupur, Ahmedabad (4.0 KM)\t40747469
Vishal Ramanikbhai Maru\t9099024319\tCharel, Ahmedabad\t
Yash Patel\t8140005157\tDudheshwar, Ahmedabad (1.9 KM)\t33476464
Pankaj Babubhai Prajapati\t8128860343\tSaraspur, Ahmedabad (4.7 KM)\t17846505
Komal Solanki\t8735951584\tSaraspur, Ahmedabad (4.9 KM)\t29326565
Gautam Aemul\t8128853254\tAsarwa, Ahmedabad (4.4 KM)\t28329977
Gautam Natvarbhai Vanzara\t6359349483\tAsarwa, Ahmedabad (4.2 KM)\t45331356
Harshida Suthar\t7433026284\tRanip, Ahmedabad (4.5 KM)\t19781307
Mamta Parmarmamtagmailcom\t9586619594\tSarangpur, Ahmedabad (4.1 KM)\t33809496
Saloni Mansuri\t9974357049\tShahpur, Ahmedabad (1.3 KM)\t37369721
Karan Parmar\t8401382156\tGirdhar Nagar, Ahmedabad (3.3 KM) | Preferred: Ahmedabad\t32195939
Pujashri Mallikarjun Bandgar\t6353486040\tBehrampura, Ahmedabad (4.5 KM)\t39889576
Mitali Bhavsar\t9904473146\tD Colony, Ahmedabad (4.6 KM)\t33747035
Parmar Neel\t8866071056\tSarangpur, Ahmedabad (4.5 KM)\t41377449
Jaydip Rao\t9313681249\tSarangpur, Ahmedabad (4.4 KM)\t40086348
Kinjal Patel\t8849444134\tDudheshwar, Ahmedabad (1.7 KM)\t45120503
Qureshi Vashil\t7383327274\tDudheshwar, Ahmedabad (1.8 KM)\t13310710
Vishal Parmar\t9904820560\tEllis Bridge, Ahmedabad (1.7 KM)\t35565593
Dev Kuchara\t9265627382\tSaraspur, Ahmedabad (3.8 KM)\t31810419
Badal Nadiya\t7600978072\tAsarwa, Ahmedabad (4.1 KM)\t11480860
Purva Bhavsar\t6354891827\tNava Wadaj, Ahmedabad (2.7 KM)\t38181910
Thakor Jitendra\t8780029612\tMadhupura, Ahmedabad (2.0 KM)\t30256289
Aman Manihar\t6355714898\tSaraspur, Ahmedabad (4.0 KM)\t39239510
Nikhil Bipinkumar Panchal\t7227859625\tAnandnagar, Ahmedabad (4.2 KM)\t34570357
Rakesh Parmar\t8849014358\tBehrampura, Ahmedabad (4.6 KM)\t4940281
Parma Nayana\t7984330539\tKankaria, Ahmedabad (4.5 KM)\t5072397"""


def parse_candidates():
    candidates = []
    for line in RAW_DATA.strip().split('\n'):
        parts = line.split('\t')
        if len(parts) < 3:
            continue
        name = parts[0].strip()
        phone = parts[1].strip()
        raw_loc = parts[2].strip()
        source_id = parts[3].strip() if len(parts) > 3 else ''

        # Strip "(X.X KM)" and "| Preferred: ..." suffixes
        clean_loc = re.sub(r'\s*\(\d+\.\d+\s*KM\)', '', raw_loc)
        clean_loc = re.sub(r'\s*\|.*$', '', clean_loc).strip()

        candidates.append({
            'name': name,
            'phone': phone,
            'mentioned_location': clean_loc,
            'source_id': source_id if source_id else None,
        })
    return candidates


async def geocode_locations(locations: list[str], api_key: str) -> dict[str, tuple]:
    """Returns {location_str: (lat, lng)} for unique locations."""
    unique = list(set(locations))
    results = {}
    async with httpx.AsyncClient(timeout=10) as http:
        for loc in unique:
            query = f"{loc}, Ahmedabad, India" if 'Ahmedabad' not in loc else loc
            resp = await http.get(
                'https://maps.googleapis.com/maps/api/geocode/json',
                params={'address': query, 'key': api_key}
            )
            data = resp.json()
            if data.get('status') == 'OK' and data['results']:
                geo = data['results'][0]['geometry']['location']
                results[loc] = (geo['lat'], geo['lng'])
                print(f"  Geocoded: {loc} → {geo['lat']:.4f}, {geo['lng']:.4f}")
            else:
                print(f"  FAILED: {loc} — {data.get('status')}")
                results[loc] = (None, None)
    return results


async def main():
    candidates = parse_candidates()
    print(f"Parsed {len(candidates)} candidates")

    api_key = os.environ['GOOGLE_MAPS_API_KEY']
    unique_locs = list({c['mentioned_location'] for c in candidates})
    print(f"Geocoding {len(unique_locs)} unique locations...")
    geo_cache = await geocode_locations(unique_locs, api_key)

    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    inserted = 0
    updated = 0
    errors = 0

    for c in candidates:
        lat, lng = geo_cache.get(c['mentioned_location'], (None, None))
        # current_location = clean neighbourhood + Ahmedabad
        loc_parts = c['mentioned_location'].split(',')
        neighbourhood = loc_parts[0].strip()
        current_location = f"{neighbourhood}, Ahmedabad" if neighbourhood else 'Ahmedabad'

        try:
            existing = await conn.fetchrow(
                "SELECT id FROM candidates WHERE phone = $1", c['phone']
            )
            if existing:
                await conn.execute("""
                    UPDATE candidates SET
                        name = $1,
                        mentioned_location = $2,
                        current_location = $3,
                        location_lat = $4,
                        location_lng = $5,
                        source = 'sourced',
                        source_id = $6,
                        is_active = false,
                        dnd_until = '2026-10-03',
                        updated_at = now()
                    WHERE phone = $7
                """, c['name'], c['mentioned_location'], current_location,
                    lat, lng, c['source_id'], c['phone'])
                updated += 1
            else:
                await conn.execute("""
                    INSERT INTO candidates
                        (name, phone, mentioned_location, current_location,
                         location_lat, location_lng, source, source_id,
                         is_active, dnd_until, created_at, updated_at)
                    VALUES ($1,$2,$3,$4,$5,$6,'sourced',$7,false,'2026-10-03',now(),now())
                """, c['name'], c['phone'], c['mentioned_location'], current_location,
                    lat, lng, c['source_id'])
                inserted += 1
        except Exception as e:
            print(f"  ERROR {c['name']} ({c['phone']}): {e}")
            errors += 1

    await conn.close()
    print(f"\nDone — inserted: {inserted}, updated: {updated}, errors: {errors}")
    total = await asyncpg.connect(os.environ['DATABASE_URL'])
    count = await total.fetchval('SELECT COUNT(*) FROM candidates')
    await total.close()
    print(f"Total candidates in DB: {count}")


if __name__ == '__main__':
    asyncio.run(main())
