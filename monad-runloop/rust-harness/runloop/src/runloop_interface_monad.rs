// Copyright (C) 2025 Category Labs, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.

use std::{
    ffi::CStr,
    ffi::CString,
    ffi::c_char,
    ffi::c_void,
    path::PathBuf,
};
use alloy_primitives::U256;
use crate::runloop::Runloop;

#[repr(C)]
struct RawMonadRunloopWord {
    bytes: [u8; 32]
}

// Opaque runloop structure:
type RawMonadRunloop = c_void;

extern "C" {
    // Deallocate a runloop client
    fn monad_runloop_delete(runloop: *mut RawMonadRunloop);

    // Execute and finalize `nblocks` number of blocks.
    fn monad_runloop_run(runloop: *mut RawMonadRunloop, nblocks: u64);

    // Store current primary (slot-encoded) state root.
    fn monad_runloop_get_primary_state_root(
        runloop: *mut RawMonadRunloop,
        result_state_root: *mut RawMonadRunloopWord,
    );

    // Store current secondary (page-encoded) state root.
    fn monad_runloop_get_secondary_state_root(
        runloop: *mut RawMonadRunloop,
        result_state_root: *mut RawMonadRunloopWord,
    );

    // Make a new runloop client on the EEST chain (EestNet), installing
    // the given genesis allocation JSON and genesis block (hex RLP)
    // when the database is fresh. The revision schedule is a comma
    // separated "<revision>:<from timestamp>" list.
    fn monad_runloop_new_eest(
        ledger_path: *const c_char,
        db_path: *const c_char,
        genesis_alloc_json: *const c_char,
        genesis_block_rlp_hex: *const c_char,
        revision_schedule: *const c_char,
    ) -> *mut RawMonadRunloop;

    // Dump the current state of the database as a JSON string.
    fn monad_runloop_dump_json(runloop: *mut RawMonadRunloop) -> *mut c_char;

    // Free a string returned by the interface.
    fn monad_runloop_free_string(str: *mut c_char);
}

pub struct MonadRunloop {
    raw: *mut RawMonadRunloop
}

impl Drop for MonadRunloop {
    fn drop(&mut self) {
        unsafe {
            monad_runloop_delete(self.raw)
        }
    }
}

impl MonadRunloop {
    pub fn new_eest(
        ledger_path: PathBuf,
        db_path: PathBuf,
        genesis_alloc_json: &str,
        genesis_block_rlp_hex: &str,
        revision_schedule: &str,
    ) -> MonadRunloop {
        let ledger_path = ledger_path.into_os_string().into_string().unwrap();
        let ledger_path = CString::new(ledger_path).unwrap();
        let db_path = db_path.into_os_string().into_string().unwrap();
        let db_path = CString::new(db_path).unwrap();
        let genesis_alloc_json = CString::new(genesis_alloc_json).unwrap();
        let genesis_block_rlp_hex =
            CString::new(genesis_block_rlp_hex).unwrap();
        let revision_schedule = CString::new(revision_schedule).unwrap();
        MonadRunloop {
            raw: unsafe {
                monad_runloop_new_eest(
                    ledger_path.as_ptr(),
                    db_path.as_ptr(),
                    genesis_alloc_json.as_ptr(),
                    genesis_block_rlp_hex.as_ptr(),
                    revision_schedule.as_ptr())
            }
        }
    }

    pub fn dump_json(&mut self) -> String {
        unsafe {
            let raw_str = monad_runloop_dump_json(self.raw);
            let result = CStr::from_ptr(raw_str)
                .to_str()
                .expect("valid utf-8 state dump")
                .to_owned();
            monad_runloop_free_string(raw_str);
            result
        }
    }
}

impl Runloop for MonadRunloop {

    fn run(&mut self, nblocks: u64) {
        unsafe {
            monad_runloop_run(self.raw, nblocks)
        }
    }

    fn get_primary_state_root(&mut self) -> U256 {
        let mut state_root = RawMonadRunloopWord{
            bytes: [0; 32],
        };
        unsafe {
            monad_runloop_get_primary_state_root(self.raw, &mut state_root)
        };
        U256::from_be_bytes(state_root.bytes)
    }

    fn get_secondary_state_root(&mut self) -> U256 {
        let mut state_root = RawMonadRunloopWord{
            bytes: [0; 32],
        };
        unsafe {
            monad_runloop_get_secondary_state_root(self.raw, &mut state_root)
        };
        U256::from_be_bytes(state_root.bytes)
    }
}
