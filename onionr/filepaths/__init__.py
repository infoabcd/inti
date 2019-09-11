from utils import identifyhome
home = identifyhome.identify_home()
if not home.endswith('/'): home += '/'

usage_file = home + 'disk-usage.txt'
block_data_location = home + 'blocks/'
contacts_location = home + 'contacts/'
public_API_host_file = home + 'public-host.txt'
private_API_host_file = home + 'private-host.txt'
bootstrap_file_location = 'static-data/bootstrap-nodes.txt'
data_nonce_file = home + 'block-nonces.dat'
forward_keys_file = home + 'forward-keys.db'
cached_storage = home + 'cachedstorage.dat'
announce_cache = home + 'announcecache.dat'
export_location = home + 'block-export/'
upload_list = home + 'upload-list.json'
config_file = home + 'config.json'

tor_hs_address_file = home + 'hs/hostname'

run_check_file = home + '.runcheck'

data_nonce_file = home + 'block-nonces.dat'

keys_file = home + 'keys.txt'