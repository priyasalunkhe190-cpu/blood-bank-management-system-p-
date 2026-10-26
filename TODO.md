# BloodBankSystem TODO

- [ ] Update landing page UI to match required heading/subtitle/nav/banner/footer/animations.
- [x] Update DB schema (SQLite) to include required donor fields (age, gender, address, city, last donation date) and password hash (with migration).

- [ ] Implement secure authentication (password hashing + verification, better secret key handling).
- [ ] Replace donor registration form with full fields and server-side validations (name alphabets only, age 18–60, mobile 10 digits, email format, strong password, confirm match).
- [ ] Ensure duplicate email registration is prevented and shown via flash messages.
- [ ] Add forgot password flow (UI + route; implement safe reset approach for demo).
- [ ] Admin: implement search donors by blood group + city.
- [ ] Admin: implement donor update and delete (CRUD).
- [ ] Admin: add charts/graphs and request status summaries.
- [ ] Implement emergency blood request feature + admin view.
- [ ] Upgrade ML features: donor eligibility prediction, blood availability analysis, nearest donor suggestion by city+blood group.
- [ ] Wire ML/nearest donor suggestion into dashboards.
- [ ] Update requirements.txt if needed.
- [ ] Run `pip install -r requirements.txt` and smoke test all flows.

