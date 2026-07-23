"""create vehicles table

Revision ID: 260035c4ba7a
Revises: 
Create Date: 2026-07-23 23:52:02.634897

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '260035c4ba7a'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tạo bảng vehicles với vehicle_id là auto-increment integer
    # SQLAlchemy sẽ tự động tạo enum type vehiclestatus
    op.create_table(
        'vehicles',
        sa.Column('vehicle_id', sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column('license_plate', sa.String(20), unique=True, nullable=False),
        sa.Column('vin', sa.String(17), unique=True, nullable=False),
        sa.Column('telematics_device_id', sa.String(50), unique=True, nullable=True),
        sa.Column('make', sa.String(50), nullable=False),
        sa.Column('model', sa.String(50), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('status', sa.Enum('active', 'inactive', 'maintenance', 'decommissioned', name='vehiclestatus'), nullable=False, server_default='active'),
        sa.Column('fleet_id', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
    )
    
    # Tạo indexes
    op.create_index('ix_vehicles_license_plate', 'vehicles', ['license_plate'])
    op.create_index('ix_vehicles_vin', 'vehicles', ['vin'])
    op.create_index('ix_vehicles_telematics_device_id', 'vehicles', ['telematics_device_id'])
    op.create_index('ix_vehicles_status', 'vehicles', ['status'])


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_vehicles_status', 'vehicles')
    op.drop_index('ix_vehicles_telematics_device_id', 'vehicles')
    op.drop_index('ix_vehicles_vin', 'vehicles')
    op.drop_index('ix_vehicles_license_plate', 'vehicles')
    
    # Drop bảng
    op.drop_table('vehicles')
    
    # Drop enum type
    op.execute("DROP TYPE IF EXISTS vehiclestatus CASCADE")
