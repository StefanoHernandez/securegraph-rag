import inspect
from openfga_sdk.models import TypeDefinition, WriteAuthorizationModelRequest

print("TypeDefinition signature:")
print(inspect.signature(TypeDefinition.__init__))

print("\nWriteAuthorizationModelRequest signature:")
print(inspect.signature(WriteAuthorizationModelRequest.__init__))
