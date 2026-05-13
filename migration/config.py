"""Configuration loader — reads and validates the .env file."""

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv


@dataclass
class ObjectMapping:
    hs_object: str       # e.g. "contacts"
    sf_id_property: str  # e.g. "salesforce_contact_id"


@dataclass
class Config:
    sf_client_id: str
    sf_client_secret: str
    sf_refresh_token: str
    sf_instance_url: str
    hs_access_token: str
    object_mappings: List[ObjectMapping]
    paid_tier: bool
    db_path: str


def load_config(env_file: str = ".env") -> Config:
    load_dotenv(env_file, override=True)

    required = [
        "SF_CLIENT_ID",
        "SF_CLIENT_SECRET",
        "SF_REFRESH_TOKEN",
        "SF_INSTANCE_URL",
        "HS_ACCESS_TOKEN",
        "HS_OBJECT_MAPPINGS",
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise ValueError(f"Missing required env vars: {', '.join(missing)}")

    raw_mappings = os.environ["HS_OBJECT_MAPPINGS"]
    mappings: List[ObjectMapping] = []
    for part in raw_mappings.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(
                f"Invalid HS_OBJECT_MAPPINGS entry '{part}'. "
                "Expected format: ObjectType:sf_property_name"
            )
        hs_object, sf_prop = part.split(":", 1)
        mappings.append(
            ObjectMapping(hs_object=hs_object.strip(), sf_id_property=sf_prop.strip())
        )

    if not mappings:
        raise ValueError("HS_OBJECT_MAPPINGS is empty — at least one mapping is required.")

    return Config(
        sf_client_id=os.environ["SF_CLIENT_ID"],
        sf_client_secret=os.environ["SF_CLIENT_SECRET"],
        sf_refresh_token=os.environ["SF_REFRESH_TOKEN"],
        sf_instance_url=os.environ["SF_INSTANCE_URL"].rstrip("/"),
        hs_access_token=os.environ["HS_ACCESS_TOKEN"],
        object_mappings=mappings,
        paid_tier=os.getenv("PAID_TIER", "false").lower() == "true",
        db_path=os.getenv("DB_PATH", "migration_state.db"),
    )
