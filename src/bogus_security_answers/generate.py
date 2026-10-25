"""Plausible answers from Faker, kept unique inside a question group."""

from __future__ import annotations

from collections.abc import Callable

from faker import Faker

from bogus_security_answers.errors import UniquenessExhausted, ValidationError
from bogus_security_answers.text import normalize_answer

ANSWER_KINDS: tuple[str, ...] = (
    "us_city",
    "last_name",
    "first_name",
    "street",
    "school",
    "employer",
    "job",
    "food",
    "color",
    "vehicle",
    "year",
)

MAX_ATTEMPTS = 40

FOODS: tuple[str, ...] = (
    "apple pie",
    "banana bread",
    "beef stew",
    "blueberry pancakes",
    "chicken soup",
    "chili",
    "chocolate cake",
    "clam chowder",
    "fried rice",
    "grilled cheese",
    "lasagna",
    "macaroni and cheese",
    "meatloaf",
    "oatmeal",
    "peach cobbler",
    "pizza",
    "pot roast",
    "pumpkin pie",
    "roast chicken",
    "spaghetti",
    "tacos",
    "tomato soup",
    "tuna casserole",
    "vegetable soup",
)

SCHOOL_SUFFIXES: tuple[str, ...] = (
    "Elementary School",
    "Middle School",
    "High School",
    "Academy",
)

VEHICLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Ford", ("Ranger", "Escape", "Mustang")),
    ("Toyota", ("Camry", "Corolla", "RAV4")),
    ("Honda", ("Civic", "Accord", "CR-V")),
    ("Chevrolet", ("Malibu", "Silverado", "Equinox")),
    ("Subaru", ("Outback", "Forester", "Impreza")),
    ("Hyundai", ("Elantra", "Sonata", "Tucson")),
)

_FAKER: Faker | None = None


def _faker() -> Faker:
    global _FAKER
    if _FAKER is None:
        _FAKER = Faker("en_US")
    return _FAKER


def _us_city(fake: Faker) -> str:
    return f"{fake.city()}, {fake.state_abbr()}"


def _last_name(fake: Faker) -> str:
    return fake.last_name()


def _first_name(fake: Faker) -> str:
    return fake.first_name()


def _street(fake: Faker) -> str:
    return fake.street_name()


def _school(fake: Faker) -> str:
    suffix = fake.random_element(elements=SCHOOL_SUFFIXES)
    return f"{fake.last_name()} {suffix}"


def _employer(fake: Faker) -> str:
    return fake.company()


def _job(fake: Faker) -> str:
    return fake.job()


def _food(fake: Faker) -> str:
    return fake.random_element(elements=FOODS)


def _color(fake: Faker) -> str:
    return fake.color_name()


def _vehicle(fake: Faker) -> str:
    make, models = fake.random_element(elements=VEHICLES)
    model = fake.random_element(elements=models)
    year = fake.random_int(min=1985, max=2024)
    return f"{year} {make} {model}"


def _year(fake: Faker) -> str:
    return str(fake.random_int(min=1920, max=2024))


_GENERATORS: dict[str, Callable[[Faker], str]] = {
    "us_city": _us_city,
    "last_name": _last_name,
    "first_name": _first_name,
    "street": _street,
    "school": _school,
    "employer": _employer,
    "job": _job,
    "food": _food,
    "color": _color,
    "vehicle": _vehicle,
    "year": _year,
}


def generate_value(kind: str, fake: Faker | None = None) -> str:
    generator = _GENERATORS.get(kind)
    if generator is None:
        raise ValidationError(f"Unknown answer type '{kind}'.")
    return generator(fake or _faker()).strip()


def generate_unique_answer(
    kind: str,
    existing: set[str],
    factory: Callable[[str], str] | None = None,
) -> str:
    """Return a new answer whose normalized form is not in ``existing``."""
    if kind not in ANSWER_KINDS:
        raise ValidationError(f"Unknown answer type '{kind}'.")
    producer = factory or (lambda answer_kind: generate_value(answer_kind))
    seen = set(existing)
    for _ in range(MAX_ATTEMPTS):
        candidate = producer(kind).strip()
        normalized = normalize_answer(candidate)
        if normalized and normalized not in seen:
            return candidate
        if normalized:
            seen.add(normalized)
    raise UniquenessExhausted()
