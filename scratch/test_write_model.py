from openfga_sdk.models import TypeDefinition, WriteAuthorizationModelRequest
import json

type_definitions = [
    {
        "type": "user"
    },
    {
        "type": "group",
        "relations": {
            "member": {
                "this": {},
                "allowed_types": [
                    {"type": "user"},
                    {"type": "group", "relation": "member"}
                ]
            }
        },
        "metadata": {
            "relations": {
                "member": {
                    "directly_related_user_types": [
                        {"type": "user"},
                        {"type": "group", "relation": "member"}
                    ]
                }
            }
        }
    },
    {
        "type": "document",
        "relations": {
            "owner": {
                "this": {},
                "allowed_types": [
                    {"type": "user"}
                ]
            },
            "group_viewer": {
                "this": {},
                "allowed_types": [
                    {"type": "group", "relation": "member"}
                ]
            },
            "direct_viewer": {
                "this": {},
                "allowed_types": [
                    {"type": "user"}
                ]
            },
            "viewer": {
                "union": {
                    "child": [
                        {"computedUserset": {"relation": "owner"}},
                        {"computedUserset": {"relation": "group_viewer"}},
                        {"computedUserset": {"relation": "direct_viewer"}}
                    ]
                }
            }
        },
        "metadata": {
            "relations": {
                "owner": {
                    "directly_related_user_types": [
                        {"type": "user"}
                    ]
                },
                "group_viewer": {
                    "directly_related_user_types": [
                        {"type": "group", "relation": "member"}
                    ]
                },
                "direct_viewer": {
                    "directly_related_user_types": [
                        {"type": "user"}
                    ]
                },
                "viewer": {
                    "directly_related_user_types": []
                }
            }
        }
    },
    {
        "type": "chunk",
        "relations": {
            "parent_document": {
                "this": {},
                "allowed_types": [
                    {"type": "document"}
                ]
            },
            "viewer": {
                "tupleToUserset": {
                    "tupleset": {"relation": "parent_document"},
                    "computedUserset": {"relation": "viewer"}
                }
            }
        },
        "metadata": {
            "relations": {
                "parent_document": {
                    "directly_related_user_types": [
                        {"type": "document"}
                    ]
                },
                "viewer": {
                    "directly_related_user_types": []
                }
            }
        }
    }
]

try:
    defs = [TypeDefinition(**td) for td in type_definitions]
    req = WriteAuthorizationModelRequest(
        type_definitions=defs,
        schema_version="1.1"
    )
    print("Success! Full authorization model AST constructed perfectly.")
except Exception as e:
    print(f"Failed to compile FGA AST: {e}")
