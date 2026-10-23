use alloy_primitives::U256;

pub trait Runloop {
    fn run(&mut self, n_blocks: u64);
    fn get_state_root(&mut self) -> U256;
}
