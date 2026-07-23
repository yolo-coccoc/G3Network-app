"""change_vehicle_id_to_auto_increment

Revision ID: 50dfe02deac1
Revises: 260035c4ba7a
Create Date: 2026-07-24 00:39:02.875187

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '50dfe02deac1'
down_revision: Union[str, None] = '260035c4ba7a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop bảng cũ (vì không thể đổi kiểu dữ liệu của PK)
    op.drop_table('vehicles')
    
    # Tạo lại bảng với vehicle_id là auto-increment integer
    op.create_table(
        'vehicles',
        sa.Column('vehicle_id', sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column('license_plate', sa.String(20), unique=True, nullable=False),
        sa.Column('vin', sa.String(17), unique=True, nullable=False),
        sa.Column('telematics_device_id', sa.String(50), unique=True, nullable=True),
        sa.Column('make', sa.String(50), nullable=False),
        sa.Column('model', sa.String(50), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('status', sa.Enum('active', 'inactive', 'maintenance', 'decommissioned', name='vehiclestatus'), nullable=False),
        sa.Column('fleet_id', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
    )
    
    # Tạo indexes
    op.create_index('ix_vehicles_license_plate', 'vehicles', ['license_plate'])
    op.create_index('ix_vehicles_vin', 'vehicles', ['vin'])
    op.create_index('ix_vehicles_telematics_device_id', 'vehicles', ['telematics_device_id'])
    op.create_index('ix_vehicles_status', 'vehicles', ['status'])


def downgrade() -> None:
    # Drop bảng mới
    op.drop_table('vehicles')
    
    # Tạo lại bảng cũ với id là UUID string
    op.create_table(
        'vehicles',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('license_plate', sa.String(20), unique=True, nullable=False),
        sa.Column('vin', sa.String(17), unique=True, nullable=False),
        sa.Column('telematics_device_id', sa.String(50), unique=True, nullable=True),
        sa.Column('make', sa.String(50), nullable=False),
        sa.Column('model', sa.String(50), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('status', sa.Enum('active', 'inactive', 'maintenance', 'decommissioned', name='vehiclestatus'), nullable=False),
        sa.Column('fleet_id', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
    )
    
    # Tạo indexes
    op.create_index('ix_vehicles_license_plate', 'vehicles', ['license_plate'])
    op.create_index('ix_vehicles_vin', 'vehicles', ['vin'])
    op.create_index('ix_vehicles_telematics_device_id', 'vehicles', ['telematics_device_id'])
    op.create_index('ix_vehicles_status', 'vehicles', ['status'])
