fn main() {
    println!("cargo:rerun-if-changed=../../monad-bft/monad-execution/rust/crates/monad-cxx");
    println!("cargo:rustc-link-lib=dylib=monad_execution");
}
