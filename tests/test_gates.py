from app.gates import evaluate, min_years, pay_lpa

PREFS = {"locations": ["Bengaluru", "Bangalore", "Hyderabad", "Remote"], "salary_floor_lpa": {"software": 10, "other": 12},
         "max_years_required": 1, "service_firms": ["Infosys", "Appinventiv"]}


def job(**kw):
    return {"title": "Software Engineer", "company": "Acme", "location": "Bengaluru, Karnataka, India", "description": "", **kw}


def test_pass_for_a_clean_job():
    assert evaluate(job(), PREFS, ["English"])["verdict"] == "pass"


def test_location_outside_list_fails():
    assert evaluate(job(location="Mumbai, Maharashtra"), PREFS, ["English"])["verdict"] == "fail"


def test_bare_india_location_is_only_a_flag():
    assert evaluate(job(location="India"), PREFS, ["English"])["verdict"] == "flag"


def test_service_firm_fails_even_with_product_sounding_title():
    assert evaluate(job(company="Appinventiv"), PREFS, ["English"])["verdict"] == "fail"


def test_staffing_industry_fails():
    assert evaluate(job(industries="Staffing and Recruiting"), PREFS, ["English"])["verdict"] == "fail"


def test_years_two_flags_three_fails_but_interns_are_exempt():
    assert evaluate(job(description="2+ years of experience"), PREFS, [])["verdict"] == "flag"
    assert evaluate(job(description="3-5 years of experience"), PREFS, [])["verdict"] == "fail"
    assert evaluate(job(title="SDE Intern", description="3+ years preferred"), PREFS, [])["verdict"] == "pass"


def test_pay_below_floor_fails_and_monthly_pay_is_annualised():
    assert evaluate(job(salary="₹5L – ₹9L"), PREFS, [])["verdict"] == "fail"
    assert pay_lpa("₹40,000 – ₹60,000 per month") == (4.8, 7.2)
    assert pay_lpa("12-18 LPA") == (12.0, 18.0)


def test_unknown_language_requirement_fails():
    r = evaluate(job(description="Fluency in Telugu is a must"), PREFS, ["English", "Hindi"])
    assert r["verdict"] == "fail" and any("Telugu" in x for x in r["reasons"])


def test_min_years_picks_smallest_requirement():
    assert min_years("5+ years overall, 1-3 years in Python") == 1
