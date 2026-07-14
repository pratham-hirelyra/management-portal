import asyncio, json, os
from dotenv import load_dotenv
load_dotenv()
import asyncpg

EVAL_SUMMARY = {
    "overview_summary": "The candidate demonstrates limited but partially functional accounting understanding. Core journal entry treatment for vendor booking, input tax classification, and basic TDS accounting is present in several answers, showing some exposure to GST and TDS-related transaction processing. However, the overall conceptual depth is inconsistent, with weakness in period-based adjustment treatment and an inability to explain a standard bank reconciliation difference, which indicates insufficient practical control-level understanding. GST and TDS involvement appears transactional rather than strongly conceptual, and bank reconciliation depth appears weak.",
    "technical_scoring": {
        "question_1": {"question": "We have received a professional consultancy bill of ₹1,00,000 from Tata Consultancy, on which 18% GST has been charged. While entering this expense, will you debit or credit the Input GST account?", "score": 5, "correct_answer": "Debit", "candidate_response": "The candidate said Input GST would be debited.", "justification": "This matches the correct accounting treatment. Input GST is debited at the time of booking the eligible purchase or expense invoice."},
        "question_2": {"question": "During the same entry, will the Tata Consultancy account be debited or credited?", "score": 5, "correct_answer": "Credit", "candidate_response": "The candidate said Tata Consultancy account would be credited.", "justification": "This is correct. The vendor account is credited while recording the payable against the bill."},
        "question_3": {"question": "Why did you say that the Tata Consultancy account should be debited/credited?", "score": 5, "correct_answer": "Because TATA consultancy is a vendor or creditor and hence must be credited.", "candidate_response": "The candidate said it would be credited because it is a creditor and a liability.", "justification": "This fully matches the knowledge base."},
        "question_4": {"question": "What is Input GST actually? Is it an asset, a liability, an income, or an expense?", "score": 5, "correct_answer": "Asset", "candidate_response": "After hesitation, the candidate answered that Input GST is an asset.", "justification": "The final answer is correct."},
        "question_5": {"question": "When you deduct TDS while making an expense entry, what will you do to the TDS Payable account—debit or credit?", "score": 5, "correct_answer": "Credit", "candidate_response": "The candidate said TDS Payable would be credited.", "justification": "This is correct."},
        "question_6": {"question": "When you make the payment of this TDS to the government, what entry will you Pass?", "score": 5, "correct_answer": "TDS Payable A/c Debited and Bank A/c Credited.", "candidate_response": "The candidate said TDS Payable would be debited and Bank would be credited.", "justification": "This matches the correct payment entry."},
        "question_7": {"question": "Out of the sales of ₹50,000 in October, goods worth ₹10,000 are returned in November. Will you modify the October entry or Pass a new entry in November?", "score": 0, "correct_answer": "Pass a new entry in November.", "candidate_response": "The candidate said he would modify the October entry.", "justification": "This is incomplete and not aligned with the expected answer."},
        "question_8": {"question": "But since the sales belonged to October, why shouldn't we modify it in October itself?", "score": 0, "correct_answer": "Because GST returns would have been filed already / Oct month books would have got closed.", "candidate_response": "No answer was provided.", "justification": "No valid response available, scored zero."},
        "question_9": {"question": "You have received a bill of ₹1,00,000 + 18% GST (₹1,18,000 total) from Infosys Pvt Ltd. You have to deduct TDS at 10%. What will be the TDS amount?", "score": 5, "correct_answer": "10,000/-", "candidate_response": "The candidate answered ten thousand.", "justification": "This is correct. TDS is calculated on the base amount only."},
        "question_10": {"question": "You are making a payment of ₹6 lakh to a contractor. If the contractor is a Private Limited Company, at what rate will TDS be deducted?", "score": 0, "correct_answer": "2%", "candidate_response": "The candidate first answered 2%, but later changed to 1.5% as final answer.", "justification": "Final answer 1.5% is incorrect."},
        "question_11": {"question": "Assume you have completed the Bank Reconciliation and have Passed all missing entries. Generally, there is still a difference between the Balance as per Books and the Balance as per Bank. Can you state any one reason for this?", "score": 0, "correct_answer": "Cheques issued but not yet presented, or Cheques deposited but not yet cleared.", "candidate_response": "The candidate said he did not know the reason.", "justification": "No valid reconciling item provided."},
        "total_score": 35,
        "max_score": 55
    },
    "hiring_decision": {
        "status": "Fail",
        "reason": "The candidate's score is 45.45%, and with a current salary of ₹25,000 the required threshold is 60%. Therefore, the candidate does not meet the Pass criteria."
    }
}

async def main():
    conn = await asyncpg.connect(dsn=os.environ["DATABASE_URL"])
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.execute("""
        UPDATE candidates SET
            state = $1,
            mentioned_location = $2,
            call_status = $3,
            category = $4,
            technical_evaluation_score = $5,
            evaluation_pdf_url = $6,
            hiring_decision_reason = $7,
            evaluation_summary = $8
        WHERE id = 'f51e85e4-87dd-4d5b-813c-c91f8ae4d145'
    """,
        'NA',
        'Prahladnagar, Ahmedabad',
        'LAST_DROPOFF',
        'Accountant',
        63.63636364,
        'https://storage.cloud.google.com/hirelyra-phone-screening-v2/7405552929/phone-screening-evaluation-7405552929.html',
        "The candidate's score is 45.45%, and with a current salary of ₹25,000 the required threshold is 60%. Therefore, the candidate does not meet the Pass criteria.",
        EVAL_SUMMARY,
    )
    print("Updated.")
    await conn.close()

asyncio.run(main())
