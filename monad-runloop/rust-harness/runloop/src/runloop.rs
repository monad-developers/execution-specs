use alloy_primitives::U256;

pub trait Runloop {
    fn run(&mut self, n_blocks: u64);
    fn get_primary_state_root(&mut self) -> U256;
    fn get_secondary_state_root(&mut self) -> U256;
}
