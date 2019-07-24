'''
    Onionr - Private P2P Communication

    This file contains the command for banning blocks from the node
'''
'''
    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
'''
import sys
import logger
from onionrutils import stringvalidators
from onionrstorage import removeblock
def ban_block(o_inst):
    try:
        ban = sys.argv[2]
    except IndexError:
        ban = logger.readline('Enter a block hash:')
    if stringvalidators.validate_hash(ban):
        if not o_inst.blacklist.inBlacklist(ban):
            try:
                o_inst.blacklist.addToDB(ban)
                removeblock.remove_block(ban)
            except Exception as error:
                logger.error('Could not blacklist block', error=error, terminal=True)
            else:
                logger.info('Block blacklisted', terminal=True)
        else:
            logger.warn('That block is already blacklisted', terminal=True)
    else:
        logger.error('Invalid block hash', terminal=True)