"""
<Program Name>
  director.py

<Purpose>
  A core module that provides needed functionality for an Uptane-compliant
  Director. This CAN remain largely unchanged in real use. It is upon this that
  a full director is built to OEM specifications. A sample of such a Director
  is in demo/demo_director.py.

  Fundamentally, this code translates lists of vehicle software assignments
  (roughly mapping ECU IDs to targets) into signed metadata suitable for sending
  to a vehicle.

  In particular, this code supports:

    - Initialization of a director given vehicle info, ECU info, ECU public
      keys, Director private keys, etc.

    - Registration of new ECUs, given serial and public key

    - Validation of Vehicle manifests

    - Validation of ECU manifests

    - Writing of signed targets metadata for a given vehicle, given as input
      a map of ecu serials to target info (or filenames from which to extract
      target info)

"""
from __future__ import unicode_literals

import uptane # Import before TUF modules; may change tuf.conf values.
import uptane.formats
import uptane.common
import uptane.services.inventorydb as inventory
import uptane.encoding.asn1_codec as asn1_codec
import tuf
import tuf.formats
import tuf.repository_tool as rt
#import uptane.ber_encoder as ber_encoder
from uptane import GREEN, RED, YELLOW, ENDCOLORS

import os
import hashlib

from uptane.encoding.asn1_codec import DATATYPE_TIME_ATTESTATION
from uptane.encoding.asn1_codec import DATATYPE_DRONE_MANIFEST
from uptane.encoding.asn1_codec import DATATYPE_SWARM_MANIFEST

log = uptane.logging.getLogger('director')
log.addHandler(uptane.file_handler)
log.addHandler(uptane.console_handler)
log.setLevel(uptane.logging.DEBUG)



class Director:
  """
  See file's docstring.

  Fields:

    key_dirroot_pri
      Private signing key for the root role in the Director's repositories

    key_dirtime_pri
      Private signing key for the timestamp role in the Director's repositories

    key_dirsnap_pri
      Private signing key for the snapshot role in the Director's repositories

    key_dirtarg_pri
      Private signing key for the targets role in the Director's repositories

    vehicle_repositories
      A dictionary of tuf.repository_tool.Repository objects, indexed by VIN.
      Each holds the Director metadata geared toward that particular vehicle.

    director_repos_dir
      The root directory in which the repositories for each vehicle reside.

  """


  def __init__(self,
    director_repos_dir,
    key_root_pri,
    key_root_pub,
    key_timestamp_pri,
    key_timestamp_pub,
    key_snapshot_pri,
    key_snapshot_pub,
    key_targets_pri,
    key_targets_pub):

    """
    """

    tuf.formats.RELPATH_SCHEMA.check_match(director_repos_dir)

    for key in [
        key_root_pri, key_root_pub, key_timestamp_pri, key_timestamp_pub,
        key_snapshot_pri, key_snapshot_pub, key_targets_pri, key_targets_pub]:
      tuf.formats.ANYKEY_SCHEMA.check_match(key)

    self.director_repos_dir = director_repos_dir

    self.key_dirroot_pri = key_root_pri
    self.key_dirroot_pub = key_root_pub
    self.key_dirtime_pri = key_timestamp_pri
    self.key_dirtime_pub = key_timestamp_pub
    self.key_dirsnap_pri = key_snapshot_pri
    self.key_dirsnap_pub = key_snapshot_pub
    self.key_dirtarg_pri = key_targets_pri
    self.key_dirtarg_pub = key_targets_pub

    self.swarm_repositories = dict()





  def register_duid(self, duid, drone_key, suid, is_fogdrone=False):
    """
    Set the expected public key for signed messages from the ECU with the given
    ECU Serial. If signed messages purportedly coming from the ECU with that
    ECU Serial are not signed by the given key, they will not be trusted.

    This also associates the ECU Serial with the given VIN, so that the
    Director will treat this ECU as part of that vehicle.

    Exceptions
      uptane.UnknownVehicle
        if the VIN is not known.

      uptane.Spoofing
        if the given ECU Serial already has a registered public key.
        (That is, this public method is not how you should replace the public
        key a given ECU uses.)

      uptane.FormatError or tuf.FormatError
        if the arguments do not fit the correct format.
    """
    uptane.formats.SUID_SCHEMA.check_match(suid)
    uptane.formats.DUID_SCHEMA.check_match(duid)
    tuf.formats.ANYKEY_SCHEMA.check_match(drone_key)

    inventory.check_suid_registered(suid)

    # Register the public key and associate the ECU with the given VIN.
    inventory.register_drone(
        is_fogdrone, suid, duid, drone_key, overwrite=False)

    log.info(
        GREEN + 'Registered a new drone, ' + repr(duid) + ' in '
        'swarm ' + repr(suid) + ' with drone public key: ' + repr(drone_key) +
        ENDCOLORS)





  def validate_drone_manifest(self, duid, signed_drone_manifest):
    """
    Arguments:
      ecuid: uptane.formats.ECU_SERIAL_SCHEMA
      manifest: uptane.formats.SIGNABLE_ECU_VERSION_MANIFEST_SCHEMA
    """
    uptane.formats.DUID_SCHEMA.check_match(duid)
    uptane.formats.SIGNABLE_DRONE_VERSION_MANIFEST_SCHEMA.check_match(
        signed_drone_manifest)

    # If it doesn't match expectations, error out here.

    if duid != signed_drone_manifest['signed']['duid']:
      raise uptane.Spoofing('Received a spoofed or mistaken manifest: supposed '
          'origin drone (' + repr(duid) + ') is not the same as what is '
          'signed in the manifest itself (' +
          repr(signed_drone_manifest['signed']['duid']) + ').')

    if duid not in inventory.duid_public_keys:
      log.info(
          'Validation failed on a Drone Manifest: DRONE ' + repr(duid) +
          ' is not registered.')
      raise uptane.UnknownECU('The Director is not aware of the given drone '
          'duid (' + repr(duid) + '. Manifest rejected. If the drone is '
          'new, Register the new drone with its key in order to be able to '
          'submit its manifests.')

    drone_public_key = inventory.duid_public_keys[duid]


    valid = uptane.common.verify_signature_over_metadata(
        drone_public_key,
        signed_drone_manifest['signatures'][0], # TODO: Fix single-signature assumption
        signed_drone_manifest['signed'],
        DATATYPE_DRONE_MANIFEST)

    if not valid:
      log.info(
          'Validation failed on a drone Manifest: signature is not valid. '
          'It must be correctly signed by the expected key for that drone.')
      raise tuf.BadSignatureError('Sender supplied an invalid signature. '
          'Drone Manifest is unacceptable. If you see this persistently, it is '
          'possible that the Fogdrone is compromised or that there is a man in '
          'the middle attack or misconfiguration.')





  def register_swarm_manifest(
      self, suid, primary_fogdrone, signed_swarm_manifest):
    """
    Saves the swarm manifest in the InventoryDB, validating first the
    Fog Drone's key on the full vehicle manifest, then each individual ECU
    Manifest's signature.

    If the Primary's signature over the whole Vehicle Manifest is invalid, then
    this raises an error (either tuf.BadSignatureError, uptane.Spoofing, or
    uptane.UnknownECU).

    Otherwise, if any of the individual ECU Manifests are invalid, those
    individual ECU Manifests are discarded, and others are processed. (No
    error is raised - only a warning.)

    Arguments:
      vin: vehicle's unique identifier, uptane.formats.VIN_SCHEMA
      primary_ecu_serial: Primary ECU's unique identifier,
                          uptane.formats.ECU_SERIAL_SCHEMA
      manifest: the vehicle manifest, as specified in the implementation
                specification and compliant with
                uptane.formats.SIGNABLE_VEHICLE_VERSION_MANIFEST_SCHEMA
                If, the metadata format is set to ASN.1/DER, then this will
                instead be compliant with uptane.formats.DER_DATA_SCHEMA,
                and will be decoded and converted back to be compliant with
                uptane.formats.SIGNABLE_VEHICLE_VERSION_MANIFEST_SCHEMA


    Exceptions:

        tuf.BadSignatureError
          if the Primary's signature on the vehicle manifest is invalid
          (An individual Secondary's signature on an ECU Version Manifests
          being invalid does not raise an exception, but instead results in
          a warning and that ECU Version Manifest alone being discarded.)

        uptane.Spoofing
          if the primary_ecu_serial argument does not match the ECU Serial
          for the Primary in the signed Vehicle Version Manifest.
          (As above, an ECU Version Manifest that is wrong in this respect is
          individually discarded with only a warning.)

        uptane.UnknownECU
          if the ECU Serial provided for the Primary is not known to this
          Director.
          (As above, an unknown Secondary ECU in an ECU Version Manifest is
          individually discarded with only a warning.)

        uptane.UnknownVehicle
          if the VIN provided is not known to this Director

    """
    uptane.formats.SUID_SCHEMA.check_match(suid)
    uptane.formats.DUID_SCHEMA.check_match(primary_fogdrone)

    if tuf.conf.METADATA_FORMAT == 'der':
      # Check format and convert back to expected vehicle manifest format.
      uptane.formats.DER_DATA_SCHEMA.check_match(signed_swarm_manifest)
      signed_swarm_manifest = asn1_codec.convert_signed_der_to_dersigned_json(
          signed_swarm_manifest, DATATYPE_SWARM_MANIFEST)

    uptane.formats.SIGNABLE_SWARM_VERSION_MANIFEST_SCHEMA.check_match(
        signed_swarm_manifest)

    if suid not in inventory.drones_by_suid:
      raise uptane.UnknownVehicle('Received a vehicle manifest purportedly '
          'from a vehicle with a SUID that is not known to this Director.')

    # Process Primary's signature on full manifest here.
    # If it doesn't match expectations, error out here.
    self.validate_fogdrone_certification_in_swarm_manifest(
        suid, primary_fogdrone, signed_swarm_manifest)

    # If the Primary's signature is valid, save the whole vehicle manifest to
    # the inventorydb.
    inventory.save_swarm_manifest(suid, signed_swarm_manifest)

    log.info(GREEN + ' Received a Swarm Manifest from Fog drone ' +
        repr(primary_fogdrone) + ', with a valid signature from that drone.' +
        ENDCOLORS)
    # TODO: Note that the above hasn't checked that the signature was from
    # a Primary, just from an ECU. Fix.


    # Validate signatures on and register all individual drone manifests for each
    # drone (may have multiple manifests per drone).
    all_drone_manifests = \
        signed_swarm_manifest['signed']['drone_version_manifests']

    for duid in all_drone_manifests:
      drone_manifests = all_drone_manifests[duid]
      for manifest in drone_manifests:
        try:
          # This calls validate_drone_manifest, which can raise the errors
          # caught below.
          self.register_drone_manifest(suid, duid, manifest)
        except uptane.Spoofing as e:
          log.warning(
              RED + 'Discarding a spoofed or malformed drone Manifest. Error '
              ' from validating that drone manifest follows:\n' + ENDCOLORS +
              repr(e))
        except uptane.UnknownECU as e:
          log.warning(
              RED + 'Discarding an drone Manifest from unknown drone. Error from '
              'validation attempt follows:\n' + ENDCOLORS + repr(e))
        except tuf.BadSignatureError as e:
          log.warning(
              RED + 'Rejecting a drone Manifest whose signature is invalid, '
              'from within an otherwise valid Swarm Manifest. Error from '
              'validation attempt follows:\n' + ENDCOLORS + repr(e))





  def validate_fogdrone_certification_in_swarm_manifest(
      self, suid, primary_fogdrone, swarm_manifest):
    """
    Check the Primary's signature on the Vehicle Manifest and any other data
    the Primary is certifying, without diving into the individual ECU Manifests
    in the Vehicle Manifest.

    Raises an exception if there is an issue with the Primary's signature.
    No return value.
    """
    # If args don't match expectations, error out here.
    log.info('Beginning validate_fogdrone_certification_in_swarm_manifest')
    uptane.formats.SUID_SCHEMA.check_match(suid)
    uptane.formats.DUID_SCHEMA.check_match(primary_fogdrone)
    uptane.formats.SIGNABLE_SWARM_VERSION_MANIFEST_SCHEMA.check_match(
        swarm_manifest)


    if primary_fogdrone!= swarm_manifest['signed']['primary_fogdrone']:
      raise uptane.Spoofing('Received a spoofed or mistaken swarm manifest: '
          'the supposed origin fogdrone (' + repr(primary_fogdrone) + ') '
          'is not the same as what is signed in the swarm manifest itself ' +
          '(' + repr(swarm_manifest['signed']['primary_fogdrone']) + ').')

    # TODO: Consider mechanism for fetching keys from inventorydb itself,
    # rather than always registering them after Director svc starts up.
    if primary_fogdrone not in inventory.duid_public_keys:
      log.debug(
          'Rejecting a swarm manifest from a fog drone whose '
          'key is not registered.')
      raise uptane.UnknownECU('The Director is not aware of the given Fog drone '
          'DUID (' + repr(primary_fogdrone) + '. Manifest rejected. If '
          'the drone is new, Register the new drone with its key in order to be '
          'able to submit its manifests.')

    drone_public_key = inventory.duid_public_keys[primary_fogdrone]

    # Here, we check to see if the key that signed the Swarm Manifest is the
    # same key as ecu_public_key (the one the director expects), so that we can
    # generate a more informative error, allowing user/debugger to distinguish
    # between a bad signature ostensibly from the right key and a signature
    # from the wrong key.
    # TODO: Fix(?) assumption that one signature is used below.
    keyid_used_in_signature = swarm_manifest['signatures'][0]['keyid']
    # Note, though, that there could be some edge cases here that the TUF code
    # might actually resolve: for example, if the keyid hash algorithm used
    # in the signature is not the same one as the one used in the key listing,
    # this check would provide a false failure. So we don't raise an error here,
    # and instead just log this difference and let the final arbiter of the
    # validity of the signature be the dedicated code in tuf.keys.
    if keyid_used_in_signature != drone_public_key['keyid']:
      log.info(
          'Key used to sign Swarm Manifest has a different keyid from that '
          'listed in the inventory DB. Expect signature validation to fail, '
          'unless the key is the same but the keyid differently hashed. '
          'Expected keyid: ' + repr(drone_public_key['keyid']) + '; keyid used '
          'in signature: ' + repr(keyid_used_in_signature))


    if tuf.conf.METADATA_FORMAT == 'der':
      # To check the signature, we have to make sure to encode the data as it
      # was when the signature was made. If we're using ASN.1/DER as the
      # data format/encoding, then we convert the 'signed' portion of the data
      # back to ASN.1/DER to check it.
      # Further, since for ASN.1/DER, a SHA256 hash is taken of the data and
      # *that* is what is signed, we perform that hashing as well and retrieve
      # the raw binary digest.
      data_to_check = asn1_codec.convert_signed_metadata_to_der(
          swarm_manifest, DATATYPE_SWARM_MANIFEST, only_signed=True)
      data_to_check = hashlib.sha256(data_to_check).digest()

    else:
      data_to_check = swarm_manifest['signed']


    valid = uptane.common.verify_signature_over_metadata(
        drone_public_key,
        swarm_manifest['signatures'][0], # TODO: Fix assumptions.
        swarm_manifest['signed'],
        DATATYPE_SWARM_MANIFEST)

    if not valid:
      log.debug(
          'Rejecting a vehicle manifest because the Primary signature on it is '
          'not valid. It must be correctly signed by the expected Primary ECU '
          'key.')
      raise tuf.BadSignatureError('Sender supplied an invalid signature. '
          'Vehicle Manifest is questionable; discarding. If you see this '
          'persistently, it is possible that there is a man in the middle '
          'attack or misconfiguration.')





  def register_drone_manifest(self, suid, duid, signed_drone_manifest):
    """
    """
    # Error out if the signature isn't valid and from the expected party.
    # Also checks argument format.
    self.validate_drone_manifest(duid, signed_drone_manifest)

    # Otherwise, we save it:
    inventory.save_drone_manifest(suid, duid, signed_drone_manifest)

    log.debug('Stored a valid Drone manifest from drone' + repr(duid))

    # Alert if there's been a detected attack.
    if signed_drone_manifest['signed']['attacks_detected']:
      log.warning(
          YELLOW + 'Attacks have been reported by the Edge Drone ' +
          repr(duid) + ':\n' +
          signed_drone_manifest['signed']['attacks_detected'] + ENDCOLORS)





  def add_new_swarm(self, suid, primary_fogdrone=None):
    """
    For adding vehicles whose VINs were not provided when this object was
    initialized.

    Note that individual ECUs should also be registered, providing their
    public keys.

    """
    # TODO: The VIN string is manipulated for create_director_repo_for_vehicle,
    # but the string is not manipulated for this addition to ecus_by_vin.
    # Treatment has to be made consistent. (In particular, things like slashes
    # are pruned - or an error is raised when they are detected.)
    inventory.register_vehicle(suid, primary_fogdrone=primary_fogdrone)

    self.create_director_repo_for_vehicle(suid)





  def create_director_repo_for_vehicle(self, suid):
    """
    Creates a separate repository object for a given vehicle identifier.
    Each uses the same keys.
    Ideally, each would use the same root.json file, but that will have to
    wait until TUF Augmentation Proposal 5 (when the hash of root.json ceases
    to be included in snapshot.json).

    The name of each repository is the VIN string.

    If the repository already exists, it is overwritten.

    Usage:

      d = uptane.services.director.Director(...)
      d.create_director_repo_for_vehicle(vin)
      d.add_target_for_ecu(vin, ecu, target_filepath)

    These repository objects can be manipulated as described in TUF
    documentation; for example, to produce metadata files afterwards for that
    vehicle:
      d.vehicle_repositories[vin].write()


    # TODO: This may be outside of the scope of the reference implementation,
    # and best to put in the demo code. It's not clear what should live in the
    # reference implementation itself for this....

    """

    uptane.formats.SUID_SCHEMA.check_match(suid)

    # Repository Tool expects to use the current directory.
    # Figure out if this is impactful and needs to be changed.
    os.chdir(self.director_repos_dir) # TODO: Is messing with cwd a bad idea?

    # Generates absolute path for a subdirectory with name equal to vin,
    # in the current directory, making (relatively) sure that there isn't
    # anything suspect like "../" in the VIN.
    # Then I strip the common prefix back off the absolute path to get a
    # relative path and keep the guarantees.
    # TODO: Clumsy and hacky; fix.
    suid = uptane.common.scrub_filename(suid, self.director_repos_dir)
    suid = os.path.relpath(suid, self.director_repos_dir)

    self.swarm_repositories[suid] = this_repo = rt.create_new_repository(
        suid, repository_name=suid)


    this_repo.root.add_verification_key(self.key_dirroot_pub)
    this_repo.timestamp.add_verification_key(self.key_dirtime_pub)
    this_repo.snapshot.add_verification_key(self.key_dirsnap_pub)
    this_repo.targets.add_verification_key(self.key_dirtarg_pub)
    this_repo.root.load_signing_key(self.key_dirroot_pri)
    this_repo.timestamp.load_signing_key(self.key_dirtime_pri)
    this_repo.snapshot.load_signing_key(self.key_dirsnap_pri)
    this_repo.targets.load_signing_key(self.key_dirtarg_pri)





  def add_target_for_drone(self, suid, duid, target_filepath):
    """
    Add a target to the repository for a swarm, marked as being for a
    specific drone.

    The target file at the provided path will be analyzed, and its hashes
    and file length will be saved in target metadata in memory, which will then
    be signed with the appropriate Director keys and written to disk when the
    "write" method is called on the vehicle repository.
    """
    uptane.formats.SUID_SCHEMA.check_match(suid)
    uptane.formats.DUID_SCHEMA.check_match(duid)
    tuf.formats.RELPATH_SCHEMA.check_match(target_filepath)

    if suid not in self.swarm_repositories:
      raise uptane.UnknownVehicle('The SUID provided, ' + repr(suid) + ' is not '
          'that of a vehicle known to this Director.')

    # With the below off, we will save targets for ECUs we didn't previously
    # know exist.
    # elif ecu_serial not in inventory.ecu_public_keys:
    #   raise uptane.UnknownECU('The ECU Serial provided, ' + repr(ecu_serial) +
    #       ' is not that of an ECU known to this Director.')

    self.swarm_repositories[suid].targets.add_target(
        target_filepath, custom={'duid': duid})
