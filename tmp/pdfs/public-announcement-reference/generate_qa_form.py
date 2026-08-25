from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from public_announcement import generate_form_a

values = {
    "corporate_debtor_name": "Mahakali Foods Private Limited",
    "date_of_incorporation": "2002-03-04",
    "registration_authority": "ROC Gwalior",
    "cin": "U15499MP2002PTC015006",
    "registered_and_principal_address": "48-Bengali Colony, Kanadiya Road, Indore, Madhya Pradesh, India, 452001",
    "cirp_commencement_date": "2026-07-31",
    "order_upload_date": "2026-08-03",
    "estimated_closure_date": "2027-01-27",
    "irp_name": "Navin Khandelwal",
    "irp_registration_number": "IBBI/IPA-001/IP-P00703/2017-18/11301",
    "irp_registered_address": "206, Navneet Plaza, 5/2 Old Palasia, Indore - 452018",
    "irp_registered_email": "navink25@yahoo.com",
    "correspondence_address": "206, Navneet Plaza, 5/2 Old Palasia, Indore - 452018",
    "process_specific_email": "cirp.mahakalifoods@gmail.com",
    "claims_submission_last_date": "2026-08-17",
    "creditor_classes": "Based on limited information, there is no class of creditor under section 21(6A)(b) of the Insolvency and Bankruptcy Code, 2016.",
    "authorised_representatives": "Not applicable.",
    "forms_weblink": "https://ibbi.gov.in/en/home/downloads",
    "authorised_representative_details": "Not applicable based on information available with the IRP.",
    "announcement_date": "2026-08-06",
    "announcement_place": "Indore",
    "afa_valid_until": "2026-12-31",
}

generate_form_a(values, Path(__file__).with_name("Public_Announcement_Form_A_Mahakali_Foods_2026-08-06.docx"))
