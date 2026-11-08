import json
from pathlib import Path
import os
from sqlalchemy import create_engine, Column, Integer, String, Float, Date
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:jrr!sZYP!J9cag@localhost:5433/sales_recovery")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define the Lead model matching the data structure
class LeadModel(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    email = Column(String)
    company = Column(String)
    industry = Column(String)
    role = Column(String)
    plan_interested = Column(String)
    deal_value = Column(Float)
    stage_lost_at = Column(String)
    last_contact_date = Column(String)
    days_inactive = Column(Integer)
    emails_sent = Column(Integer)
    emails_opened = Column(Integer)
    meetings_held = Column(Integer)
    crm_notes = Column(String)

def import_customers():
    json_path = Path("data/customers.json")
    if not json_path.exists():
        print("❌ Error: customers.json file not found! Please run the generator script first.")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        customers_data = json.load(f)

    db = SessionLocal()
    try:
        # Clear old data to avoid duplicates
        db.query(LeadModel).delete()
        
        for c in customers_data:
            # Remove internal ground-truth field if present to avoid database errors
            c.pop("_true_loss_reason", None)
            lead = LeadModel(**c)
            db.add(lead)
        
        db.commit()
        print(f"✅ Successfully imported {len(customers_data)} customers into the database on port 5433!")
    except Exception as e:
        db.rollback()
        print(f"❌ An error occurred during import: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    import_customers()