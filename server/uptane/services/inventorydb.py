"""
<Program Name>
  inventorydb.py

<Purpose>
  Interface for storing data describing the software state of vehicles served
  by the Director.


  # TODO: <~> This is not properly thread-safe: there may be race conditions if
  Primaries are being switched frequently. The windows are extremely small, but
  assertions related to VIN registration in _check_registration_is_sane() are
  likely to fail if such a race   condition is encountered. Similar race
  conditions may exist. Cautious use of mutexes or a queue is probably
  necessary.



<Globals>
  The following five global dictionaries store information about ECUs and
  vehicles, including their serials, keys, and manifests submitted from
  (ostensibly) them to the Director.

    vehicle_manifests

      A dictionary indexed by the VINs (vehicle identification numbers) of
      known vehicles (uptane.format.VIN_SCHEMA), with values each being lists
      of vehicle manifests from that vehicle - each list element is a manifest
      with structure complying with the format specification
      uptane.formats.SIGNABLE_VEHICLE_VERSION_MANIFEST_SCHEMA.

      All known vehicles should be in this dictionary.

      e.g. {'vin1': [<vehiclemanifest>, <vehiclemanifest>, ...}], 'vin2': []}


    ecu_manifests

      A dictionary indexed by the ECU Serials of known ECUs
      (uptane.format.ECU_SERIAL_SCHEMA), with values each being lists of ECU
      manifests from that ECU. Individual list elements comply with
      uptane.formats.SIGNABLE_ECU_VERSION_MANIFEST_SCHEMA.

      This is duplicated data, as all ECU Manifests were extracted from Vehicle
      Manifests which are also saved in full in global vehicle_manifests.

      All known ECU Serials should be in this dictionary.

      e.g. {'ecuserial1': [<ecumanifest>, <ecumanifest>], 'ecuserial2': []}


    primary_ecus_by_vin

      A dictionary mapping VIN of a vehicle (uptane.formats.VIN_SCHEMA) to the
      ECU Serial (uptane.formats.ECU_SERIAL_SCHEMA) of the vehicle's Primary
      ECU.

      All known vehicles should be in this dictionary. If a vehicle has no
      registered Primary ECU for some reason, the value should be set to None.

      e.g. {'vin1': 'ecuserial1', 'vin2': 'ecuserial2'}


    ecus_by_vin

      A dictionary mapping VIN of a vehicle (uptane.formats.VIN_SCHEMA) to a
      list of the ECU Serials (uptane.formats.ECU_SERIAL_SCHEMA) of all ECUs
      associated with that vehicle.

      All known vehicles should have their VIN in this dictionary.

      e.g.
          {'vin1': ['ecuserial1', 'ecuserial9'],
           'vin2': ['ecuserial2']}

      This is used to both identify known VINs and associate ECUs with VINs.
      Identifying known ECUs is generally done instead by checking the
      ecu_public_keys field, since that is flat.


    duid_public_keys

      A dictionary mapping duid (uptane.formats.ECU_SERIAL_SCHEMA) of a drone
      to the public key (conforming to uptane.formats.ANYKEY_SCHEMA) that
      corresponds to the signing key we expect that drone to use.

      All known ECUs should have their ECU Serial in this dictionary.

      e.g. {'ecuserial1': <key>, 'ecuserial2': <key>, ...}


<Public Functions>

  Registration:
    register_ecu(is_primary, vin, ecu_serial, public_key, overwrite=True)
    check_ecu_registered(ecu_serial)
    check_vin_registered(vin)

  Get Public Key:
    get_ecu_public_key(ecu_serial)

  Save Manifests:
    save_vehicle_manifest(vin, signed_vehicle_manifest)
    save_ecu_manifest(vin, ecu_serial, signed_ecu_manifest)

  Get Manifests:
    get_vehicle_manifests(vin)
    get_last_vehicle_manifest(vin)
    get_ecu_manifests(ecu_serial)
    get_last_ecu_manifest(ecu_serial)
    get_all_ecu_manifests_from_vehicle(vin)

"""
from __future__ import print_function
from __future__ import unicode_literals
from io import open

import uptane # Import before TUF modules; may change tuf.conf values.
import uptane.formats
import tuf

# Global dictionaries
swarm_manifests = {}
drone_manifests = {}
fog_drone_by_suid = {}
drones_by_suid = {}
duid_public_keys = {}


def get_ecu_public_key(ecu_serial):
  """
  Returns the public key that a particular ECU was registered with.

  <Exceptions>
    uptane.FormatError
      if ecu_serial is not a valid ecu_serial per
      uptane.formats.ECU_SERIAL_SCHEMA

    uptane.UnknownECU
      if the given ECU Serial has not been registered with a public key
  """

  uptane.formats.DUID_SCHEMA.check_match(ecu_serial)

  if ecu_serial not in duid_public_keys:
    raise uptane.UnknownECU('The given ECU Serial, ' + repr(ecu_serial) +
        ' is not known. It must be registered.')

  return duid_public_keys[ecu_serial]





def get_vehicle_manifests(vin):
  check_suid_registered(vin)
  return swarm_manifests[vin]





def get_last_vehicle_manifest(vin):
  check_suid_registered(vin)
  if not swarm_manifests[vin]:
    return None
  else:
    return swarm_manifests[vin][-1]





def get_ecu_manifests(duid):
  check_drone_registered(duid)
  return drone_manifests[duid]





def get_last_ecu_manifest(ecu_serial):
  check_drone_registered(ecu_serial)
  if not drone_manifests[ecu_serial]:
    return None
  else:
    return drone_manifests[ecu_serial][-1]




def save_swarm_manifest(suid, signed_swarm_manifest):
  """
  Given a manifest of form
  uptane.formats.SIGNABLE_VEHICLE_VERSION_MANIFEST_SCHEMA, save it in an index
  by suid, and save the individual ecu attestations in an index by ecu serial.
  """
  check_suid_registered(suid) # check arg format and registration

  uptane.formats.SIGNABLE_SWARM_VERSION_MANIFEST_SCHEMA.check_match(
       signed_swarm_manifest)

  swarm_manifests[suid].append(signed_swarm_manifest)


  # Not doing it this way because the Director is going to pass through a
  # correctly-signed vehicle manifest even if some of the ECU Manifests within
  # it are *not* correctly signed. The Director will instead issue a
  # save_ecu_manifest call for each validly-signed ECU Manifest.
  # # Save all the contained ECU manifests.
  # all_contained_ecu_manifests = signed_vehicle_manifest['signed'][
  #     'ecu_version_manifests']

  # for ecu_serial in all_contained_ecu_manifests:
  #   for signed_ecu_manifest in all_contained_ecu_manifests[ecu_serial]:
  #     save_ecu_manifest(ecu_serial, signed_ecu_manifest)





def get_all_ecu_manifests_from_vehicle(vin):
  """
  Returns a dictionary of lists of manifests, indexed by the ECU Serial of each
  ECU associated with the given VIN. (This is the same format as the
  ecu_manifests global, but only includes those ECUs associated with the
  vehicle.)

  e.g.
    {'ecuserial1': [<ecumanifest>, <ecumanifest>],
     'ecuserial9': []}
  """

  check_suid_registered(vin) # check arg format and registration

  ecus_in_vehicle = drones_by_suid[vin]

  return {serial: drone_manifests[serial] for serial in ecus_in_vehicle}





def save_drone_manifest(suid, duid, signed_drone_manifest):

  check_drone_registered(duid) # check format and registration

  uptane.formats.SIGNABLE_DRONE_VERSION_MANIFEST_SCHEMA.check_match(
       signed_drone_manifest)

  drone_manifests[duid].append(signed_drone_manifest)





def register_drone(is_fogdrone, suid, duid, public_key, overwrite=True):
  """
  Registers the ECU with the given ECU Serial, saving its public key, making
  note of the vehicle with which it is associated, and, if is_primary is True,
  marks it as the Primary ECU for the vehicle.

  Also registers the given VIN if it was not previously known (creating
  appropriate entries in the global dictionaries).

  If overwrite is False:
    if it is given an already-known ECU Serial, or if is_primary is True and
    the given VIN is already associated with a Primary ECU, raises an
    uptane.Spoofing exception.

  If overwrite is True:
    if given an already-known ECU Serial, will overwrite the previously
    registered public key and delete existing ECU Manifests for that ECU Serial.
    if given an already-known VIN that is already associated with a Primary
    ECU, it will associate the new ECU as the VIN's Primary.
    This can orphan previously-Primary ECUs:
    If a new ECU is registered as the Primary for a known vehicle that already
    had a Primary, the old Primary ECU will still be kept as a known ECU,
    along with all its ECU Manifests, and its association with the VIN is not
    removed, but it is no longer marked as the Primary for that VIN.

  Will not add the same ECU Serial to a vehicle's list of ECUs
  (ecus_by_vin[vin]) twice.
  """

  tuf.formats.BOOLEAN_SCHEMA.check_match(is_fogdrone)
  uptane.formats.SUID_SCHEMA.check_match(suid)
  uptane.formats.DUID_SCHEMA.check_match(duid)
  tuf.formats.ANYKEY_SCHEMA.check_match(public_key)
  tuf.formats.BOOLEAN_SCHEMA.check_match(overwrite)

  assert (duid in duid_public_keys) == (duid in drone_manifests), \
      'Programming error: ECU registration is not consistent.'

  if not overwrite:

    # If we aren't supposed to be overwriting public keys or Primary
    # associations, make sure we don't.

   

    if duid in duid_public_keys:
      raise uptane.Spoofing('The given DUID, ' + repr(duid) +
          ', is already associated with a public key.')

  # It is expected that the vehicle to which this ECU belongs is already
  # registered.
  check_suid_registered(suid)


  # Associate the ECU with the vehicle.
  if duid not in drones_by_suid[suid]:
    drones_by_suid[suid].append(duid)

  if is_fogdrone:
    # Set the ECU as the vehicle's Primary ECU.
    fog_drone_by_suid[suid] = duid


  # Save the ECU's public key.
  duid_public_keys[duid] = public_key


  # Create an entry in the ecu_manifests dictionary for future manifests from
  # the ECU.
  drone_manifests[duid] = []





def register_vehicle(suid, primary_fogdrone=None, overwrite=True):

  _check_registration_is_sane(suid)

  if primary_fogdrone is not None:
    uptane.formats.DUID_SCHEMA.check_match(primary_fogdrone)

  tuf.formats.BOOLEAN_SCHEMA.check_match(overwrite)

  if not overwrite and suid in drones_by_suid:
    raise uptane.Spoofing('The given VIN, ' + repr(suid) + ', is already '
        'registered.')

  drones_by_suid[suid] = []
  swarm_manifests[suid] = []
  fog_drone_by_suid[suid] = primary_fogdrone




def check_suid_registered(suid):

  _check_registration_is_sane(suid)

  if suid not in swarm_manifests:
    # TODO: Should we also log here? Review logging before exceptions
    # throughout the reference implementation.
    raise uptane.UnknownVehicle('The given SUID, ' + repr(suid) + ', is not '
        'known.')





def _check_registration_is_sane(suid):
  """
  Asserts that a data structure invariant remains correct. A vehicle must be
  in all three of the relevant global dictionaries if it is registered, and in
  none of them if it is not.
  """

  uptane.formats.SUID_SCHEMA.check_match(suid)

  # A VIN may be in either none or all three of these dictionaries, and nowhere
  # in between, or there is a bug.
  assert (suid in swarm_manifests) == (suid in drones_by_suid) == (
      suid in fog_drone_by_suid), 'Programming error.'





def check_drone_registered(duid):

  uptane.formats.DUID_SCHEMA.check_match(duid)

  if duid not in duid_public_keys:
    raise uptane.UnknownECU('The given DUID, ' + repr(duid) +
        ', is not known.')
