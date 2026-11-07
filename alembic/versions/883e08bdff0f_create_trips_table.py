"""create trips table

Revision ID: 883e08bdff0f
Revises: 6a053ca3823b
Create Date: 2026-03-31 04:46:38.684569

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '883e08bdff0f'
down_revision: Union[str, Sequence[str], None] = '6a053ca3823b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE trips (
        trip_id BIGSERIAL PRIMARY KEY,
        vendor_id SMALLINT,
        tpep_pickup_datetime TIMESTAMP,
        tpep_dropoff_datetime TIMESTAMP,
        passenger_count SMALLINT,
        trip_distance REAL,
        ratecode_id SMALLINT,
        store_and_fwd_flag CHAR(1) CHECK (store_and_fwd_flag IN ('Y','N')),
        pu_location_id INT,
        do_location_id INT,
        payment_type SMALLINT,
        fare_amount REAL,
        extra REAL,
        mta_tax REAL,
        tip_amount REAL,
        tolls_amount REAL,
        improvement_surcharge REAL,
        total_amount REAL,
        congestion_surcharge REAL,
        airport_fee REAL,
        cbd_congestion_fee REAL
    );   
    """)

def downgrade() -> None:
    op.drop_table('trips')