"""Shared fixtures: a small table with known duplicates."""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture
def leads_frame() -> pd.DataFrame:
    """Six records containing three deliberate duplicate relationships.

    Rows 0/1 share a phone with different formatting; rows 0/2 repeat the same
    contact with the primary and alternate phone swapped; rows 3/4 share an
    email. Row 5 matches nothing.
    """
    return pd.DataFrame(
        [
            {
                "Id": "1",
                "Address": "123 Main St",
                "City": "Tucson",
                "State": "AZ",
                "Owner 1 First Name": "Jane",
                "Owner 1 Last Name": "Doe",
                "Phone 1": "(520) 555-0142",
                "Phone 2": "520-555-9911",
                "Email 1": "jane@example.com",
            },
            {
                "Id": "2",
                "Address": "123 Main Street",
                "City": "Tucson",
                "State": "AZ",
                "Owner 1 First Name": "Jane",
                "Owner 1 Last Name": "Doe",
                "Phone 1": "5205550142",
                "Phone 2": "",
                "Email 1": "",
            },
            {
                "Id": "3",
                "Address": "123 Main St.",
                "City": "Tucson",
                "State": "AZ",
                "Owner 1 First Name": "Jane",
                "Owner 1 Last Name": "Doe",
                "Phone 1": "520-555-9911",
                "Phone 2": "1-520-555-0142",
                "Email 1": "jane@example.com",
            },
            {
                "Id": "4",
                "Address": "77 Oak Ave",
                "City": "Phoenix",
                "State": "AZ",
                "Owner 1 First Name": "Sam",
                "Owner 1 Last Name": "Reyes",
                "Phone 1": "6025551234",
                "Phone 2": "",
                "Email 1": "sam@example.com",
            },
            {
                "Id": "5",
                "Address": "77 Oak Avenue",
                "City": "Phoenix",
                "State": "AZ",
                "Owner 1 First Name": "Samuel",
                "Owner 1 Last Name": "Reyes",
                "Phone 1": "",
                "Phone 2": "",
                "Email 1": "SAM@example.com ",
            },
            {
                "Id": "6",
                "Address": "900 Pine Rd",
                "City": "Austin",
                "State": "TX",
                "Owner 1 First Name": "Alex",
                "Owner 1 Last Name": "Kim",
                "Phone 1": "5125557777",
                "Phone 2": "",
                "Email 1": "alex@example.com",
            },
        ]
    )
