"""Shared provider search choices used by declarative archive schemas."""
from __future__ import annotations

from ..models import SearchOption


US_STATES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "District of Columbia", "Florida", "Georgia",
    "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas",
    "Utah", "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin",
    "Wyoming",
)

RESULT_TYPES = (
    SearchOption("newspaper", "Newspapers"),
    SearchOption("book", "Books / printed material"),
    SearchOption("text", "Other text"),
    SearchOption("image", "Photos / images"),
    SearchOption("map", "Maps"),
    SearchOption("manuscript", "Manuscripts"),
    SearchOption("audio", "Audio"),
    SearchOption("video", "Film / video"),
    SearchOption("all", "All material types"),
)

STANDARD_SORTS = (
    SearchOption("relevance", "Best match"),
    SearchOption("newest", "Newest first"),
    SearchOption("oldest", "Oldest first"),
    SearchOption("title", "Title A–Z"),
)
