from __future__ import absolute_import

import appscale
import boto
import os
import tarfile
import tempfile
import uuid
import yaml

from appscale.tools.appscale_logger import AppScaleLogger
from appscale.tools.local_state import LocalState
from boto.s3.connection import OrdinaryCallingFormat


class AppscaleStorageException(Exception):
    """Error related to AppScale Cloud Storage use"""
    pass


class StorageHelper(object):
    """StorageHelper provides a simple interface to interact with appscale
    cloud storage.

    This includes the ability to upload application resources for deployment.
    """

    @classmethod
    def _get_gcs_configuration(cls):
        """ Load gcs configuration from the AppScalefile

        Returns:
              A tuple of the scheme, host and port for gcs connections
        """
        apps = appscale.tools.appscale.AppScale()
        apps_file_yaml = apps.read_appscalefile()
        apps_file = yaml.safe_load(apps_file_yaml)
        if not apps_file.get('gcs'):
            raise AppscaleStorageException('Cloud storage (gcs) not configured in AppScalefile')
        apps_file_gcs = apps_file.get('gcs')
        try:
            return (
                apps_file_gcs.get('scheme', 'http'),
                apps_file_gcs.get('host', '127.0.0.1'),
                int(apps_file_gcs.get('port', 5000))
            )
        except ValueError:
            raise AppscaleStorageException('Invalid cloud storage (gcs) configuration in AppScalefile')

    @classmethod
    def _upload_file_to_storage(cls, app_id, keyname, file_path):
        """Uploads the given file to the default appscale cloud storage
        staging bucket. If the bucket does not exist it is created for the
        given application (project)

        Args:
          app_id:  The project to use for this application.
          file_path: The full path to the file being uploaded.

        Returns:
          A str for the url of the uploaded file.
        """
        bucket = app_id
        key = "{0}/{1}".format(app_id, os.path.basename(file_path))
        secret = LocalState.get_secret_key(keyname)
        gs_scheme, gs_host, gs_port = cls._get_gcs_configuration()
        headers = {"x-goog-project-id": app_id, "AppScale-Secret": secret}
        gs = boto.connect_s3(is_secure=False, port=gs_port, host=gs_host, anon=True,
                             calling_format=OrdinaryCallingFormat())
        gb = gs.lookup(bucket, headers=headers)
        if not gb:
            gb = gs.create_bucket(bucket, headers=headers)
        gk = gb.get_key(key, validate=False)
        if gk.exists():
            raise AppscaleStorageException("Storage file already exists {0}".format(file_path))
        with open(file_path, 'r+') as archive_file:
            gk.set_contents_from_file(archive_file)
        return "gs://{0}/{1}".format(bucket, key)

    @classmethod
    def copy_app_to_storage(cls, app_location, app_id, keyname, is_verbose,
                            extras=None, custom_service_yaml=None):
        """Copies the given application to a machine running the Login service
        within an AppScale deployment.

        Args:
          app_location: The location on the local filesystem where the application
            can be found.
          app_id: The project to use for this application.
          keyname: The name of the SSH keypair that uniquely identifies this
            AppScale deployment.
          is_verbose: A bool that indicates if we should print the commands we exec
            to copy the app to the remote host to stdout.
          extras: A dictionary containing a list of files to include in the upload.
          custom_service_yaml: A string specifying the location of the service
            yaml being deployed.

        Returns:
          A str corresponding to the location in cloud storage where the
            application was copied to.
        """
        AppScaleLogger.log("Tarring application")
        rand = str(uuid.uuid4()).replace('-', '')[:8]
        local_tarred_app = "{0}/appscale-app-{1}-{2}.tar.gz". \
            format(tempfile.gettempdir(), app_id, rand)

        # Collect list of files that should be included in the tarball.
        app_files = {}
        for root, _, filenames in os.walk(app_location, followlinks=True):
            relative_dir = os.path.relpath(root, app_location)
            for filename in filenames:
                # Ignore compiled Python files.
                if filename.endswith('.pyc'):
                    continue
                relative_path = os.path.join(relative_dir, filename)
                app_files[relative_path] = os.path.join(root, filename)

        if extras is not None:
            app_files.update(extras)

        with tarfile.open(local_tarred_app, 'w:gz') as app_tar:
            for tarball_path, local_path in app_files.items():
                # Replace app.yaml with the service yaml being deployed.
                if custom_service_yaml and os.path.normpath(tarball_path) == 'app.yaml':
                    continue

                app_tar.add(local_path, tarball_path)

            if custom_service_yaml:
                app_tar.add(custom_service_yaml, 'app.yaml')

        AppScaleLogger.log("Uploading application")
        storage_url_app_tar = cls._upload_file_to_storage(app_id, keyname, local_tarred_app)

        AppScaleLogger.verbose("Removing local copy of tarred application",
                               is_verbose)
        os.remove(local_tarred_app)
        return storage_url_app_tar
