"""Operation roster snapshots; planning roles never change AA permissions."""

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from eveuniverse.models import EveSolarSystem

from .models import OperationRole, OperationRoleTemplate


def user_label(user):
    """Prefer the main character, with a safe fallback for any AA account."""
    character = getattr(getattr(user, "profile", None), "main_character", None)
    return character.character_name if character else user.get_username()


def roster_payload(operation):
    """Serialize a role-first roster, including unfilled roles and unroled users."""
    if not operation:
        return {"participants": [], "roles": [], "people": {}}
    roles = list(operation.roles.prefetch_related("users__profile__main_character"))
    participants = list(
        operation.participants.select_related("profile__main_character")
    )
    people = {user.pk: user for user in participants}
    role_rows = []
    for role in roles:
        users = list(role.users.all())
        people.update({user.pk: user for user in users})
        role_rows.append(
            {
                "name": role.name,
                "systems": role.systems,
                "users": [user.pk for user in users],
                "user_systems": {
                    str(user.pk): role.user_systems.get(str(user.pk), [])
                    for user in users
                },
                "template_id": role.template_id,
            }
        )
    return {
        "participants": [user.pk for user in participants],
        "roles": role_rows,
        "people": {str(pk): user_label(user) for pk, user in people.items()},
    }


def template_payload():
    """Copy canonical system names, never live references that edits could mutate."""
    return [
        {
            "id": role.pk,
            "name": role.name,
            "systems": [system.name for system in role.systems.all()],
        }
        for role in OperationRoleTemplate.objects.prefetch_related("systems")
    ]


def clean_systems(value, *, canonicalize=True):
    """Allow canonical names and operation-only text for unknown systems."""
    if not isinstance(value, list) or len(value) > 100:
        raise forms.ValidationError("Use at most 100 systems per role.")
    systems = []
    seen = set()
    for name in value:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise forms.ValidationError(
                "Each system needs a name of 1 to 100 characters."
            )
        name = name.strip()
        if name.casefold() not in seen:
            systems.append(name)
            seen.add(name.casefold())
    if not canonicalize:
        return systems
    canonical = {
        name.casefold(): name
        for name in EveSolarSystem.objects.filter(name__in=systems).values_list(
            "name", flat=True
        )
    }
    return [canonical.get(name.casefold(), name) for name in systems]


def clean_user_systems(value, users, systems):
    """A user's coverage is an explicit subset of their role's system list."""
    if not isinstance(value, dict) or len(value) > 500:
        raise forms.ValidationError("Choose valid system assignments for role users.")
    available = {name.casefold(): name for name in systems}
    coverage = {}
    for key, names in value.items():
        if (
            not isinstance(key, str)
            or len(key) > 20
            or not key.isdecimal()
            or key != str(int(key))
            or int(key) not in users
        ):
            raise forms.ValidationError(
                "System coverage must belong to a user selected for this role."
            )
        selected = clean_systems(names, canonicalize=False)
        if any(name.casefold() not in available for name in selected):
            raise forms.ValidationError(
                "Each user's systems must be selected in the role's system list."
            )
        coverage[key] = [available[name.casefold()] for name in selected]
    return coverage


def clean_roster(value, operation):
    """Validate all users and limits before writing any roster changes."""
    if value in (None, ""):
        return None
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("roles"), list)
        or not isinstance(value.get("participants"), list)
    ):
        raise forms.ValidationError("Choose operation roles and participants again.")
    if len(value["roles"]) > 100:
        raise forms.ValidationError("Use at most 100 roles per operation.")
    roles, names, all_users = [], set(), set()

    def ids(values):
        if (
            not isinstance(values, list)
            or len(values) > 500
            or any(
                (not isinstance(pk, int) or isinstance(pk, bool) or pk <= 0)
                for pk in values
            )
        ):
            raise forms.ValidationError("Choose valid operation users.")
        return sorted(set(values))

    participants = ids(value["participants"])
    all_users.update(participants)
    for role in value["roles"]:
        if not isinstance(role, dict) or not isinstance(role.get("name"), str):
            raise forms.ValidationError("Each role needs a name.")
        name = role["name"].strip()
        if not 1 <= len(name) <= 200 or name.casefold() in names:
            raise forms.ValidationError(
                "Role names must be unique and 1 to 200 characters long."
            )
        names.add(name.casefold())
        users = ids(role.get("users", []))
        all_users.update(users)
        template = role.get("template_id")
        if template is not None and (
            not isinstance(template, int) or isinstance(template, bool) or template <= 0
        ):
            raise forms.ValidationError("Choose a valid role template.")
        systems = clean_systems(role.get("systems", []))
        roles.append(
            {
                "name": name,
                "systems": systems,
                "users": users,
                "user_systems": clean_user_systems(
                    role.get("user_systems", {}), set(users), systems
                ),
                "template_id": template,
            }
        )
    if len(all_users) > 500:
        raise forms.ValidationError("Use at most 500 participants per operation.")
    allowed = get_user_model().objects.filter(is_active=True)
    if operation and operation.pk:
        allowed = (
            get_user_model()
            .objects.filter(
                Q(is_active=True)
                | Q(timer_operations=operation)
                | Q(operation_roles__operation=operation)
            )
            .distinct()
        )
    users = {
        user.pk: user
        for user in allowed.filter(pk__in=all_users).select_related(
            "profile__main_character"
        )
    }
    if all_users - users.keys():
        raise forms.ValidationError(
            "One or more selected users are unavailable. Choose active AA users."
        )
    templates = set(
        OperationRoleTemplate.objects.filter(
            pk__in=[role["template_id"] for role in roles if role["template_id"]]
        ).values_list("pk", flat=True)
    )
    for role in roles:
        if role["template_id"] not in templates:
            role["template_id"] = None
    return {
        "participants": participants,
        "roles": roles,
        "people": {str(pk): user_label(user) for pk, user in users.items()},
    }


def save_roster(operation, value):
    """Replace operation snapshots atomically, leaving global templates untouched."""
    if value is None:
        return
    operation.participants.set(
        set(value["participants"])
        | {user for role in value["roles"] for user in role["users"]}
    )
    operation.roles.all().delete()
    for position, row in enumerate(value["roles"]):
        role = OperationRole.objects.create(
            operation=operation,
            position=position,
            **{
                key: row[key]
                for key in ("name", "systems", "template_id", "user_systems")
            },
        )
        role.users.set(row["users"])
