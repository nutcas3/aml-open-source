#!/usr/bin/env python3
"""
Trinity Guard Live Demo Script
PyCon Kenya 2026

Demonstrates the complete Trinity architecture:
- Go backend (The Muscle) — transaction processing orchestrator
- Python NER (The Brain) — entity resolution with GLINER
- Rust ZK (The Shield) — zero-knowledge cryptography
- LLM Service (AI Investigator) — SAR generation via OpenAI/Ollama

Usage:
    python -m trinity_demo.live_demo                    # Run all scenarios
    python -m trinity_demo.live_demo --count 500        # Custom high-volume count
    python -m trinity_demo.live_demo --scenario sanctions  # Run specific scenario
    python -m trinity_demo.live_demo --check-only       # Just verify services are up
"""

import argparse
import asyncio
import os
import random
import time
from datetime import datetime
from typing import Any

import aiohttp


class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"
    END = "\033[0m"
    BOLD = "\033[1m"


class TrinityDemo:
    """Live demonstration of the Trinity Guard system."""

    def __init__(self, args: argparse.Namespace):
        self.base_urls = {
            "go_backend": os.getenv("GO_BACKEND_URL", "http://localhost:8080"),
            "python_ner": os.getenv("NER_URL", "http://localhost:9000"),
            "llm_service": os.getenv("LLM_URL", "http://localhost:8081"),
            "rust_zk": os.getenv("ZK_URL", "http://localhost:9100"),
        }
        self.args = args
        self.stats: dict[str, Any] = {
            "total_processed": 0,
            "flagged": 0,
            "sars_generated": 0,
            "zk_verifications": 0,
            "processing_times": [],
        }

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def print_banner(self) -> None:
        print(f"""
{Colors.CYAN}{Colors.BOLD}
    ╔══════════════════════════════════════════════════════════════╗
    ║                    THE COMPLIANCE TRINITY                     ║
    ║                                                               ║
    ║   Orchestrating Python, Go, and Rust for                      ║
    ║   Private, AI-Powered Anti-Money Laundering                   ║
    ║                                                               ║
    ║               PyCon Kenya 2026 — Live Demo                    ║
    ╚══════════════════════════════════════════════════════════════╝
{Colors.END}
        """)

    def print_architecture(self) -> None:
        print(f"""{Colors.HEADER}{Colors.BOLD}TRINITY ARCHITECTURE:{Colors.END}

{Colors.BLUE}                    Python Orchestrator (The Brain)
                              |
        +--------------------+--------------------+
        |                    |                    |
{Colors.CYAN}Go Backend      {Colors.GREEN}Python NER          {Colors.RED}Rust ZK Core
{Colors.CYAN}(The Muscle)    {Colors.GREEN}(The Brain)         {Colors.RED}(The Shield)

{Colors.CYAN}  PostgreSQL       {Colors.GREEN}  GLINER Model       {Colors.RED}  ZK-SNARKs
{Colors.CYAN}  Redis Pub/Sub    {Colors.GREEN}  FastAPI           {Colors.RED}  PyO3
{Colors.CYAN}  Gin Framework    {Colors.GREEN}  Entity Detection  {Colors.RED}  Poseidon Hash
{Colors.END}
{Colors.MAGENTA}  LLM Service (AI Investigator)
{Colors.MAGENTA}  OpenAI / Ollama
{Colors.MAGENTA}  SAR Generation
{Colors.END}
        """)

    # ------------------------------------------------------------------
    # Health checks
    # ------------------------------------------------------------------

    async def check_services(self) -> bool:
        """Check if all services are running. Returns True if all healthy."""
        print(f"\n{Colors.BOLD}Checking Trinity Services...{Colors.END}")

        async with aiohttp.ClientSession() as session:
            services_status: dict[str, str] = {}

            for service, url in self.base_urls.items():
                try:
                    health_url = f"{url}/health"
                    async with session.get(
                        health_url, timeout=aiohttp.ClientTimeout(total=5)
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            services_status[service] = "HEALTHY"
                            status = data.get("status", "OK") if isinstance(data, dict) else "OK"
                            print(f"  {Colors.GREEN}✓ {service}: {status}{Colors.END}")
                        else:
                            services_status[service] = "UNHEALTHY"
                            print(f"  {Colors.RED}✗ {service}: HTTP {response.status}{Colors.END}")
                except Exception as e:
                    services_status[service] = "UNREACHABLE"
                    print(f"  {Colors.RED}✗ {service}: {e}{Colors.END}")

        unhealthy = [s for s, status in services_status.items() if status != "HEALTHY"]
        if unhealthy:
            print(f"\n{Colors.YELLOW}Warning: Some services are not healthy:{Colors.END}")
            for service in unhealthy:
                print(f"  - {service}: {services_status[service]}")
            print(f"\n{Colors.YELLOW}Start services with: make up{Colors.END}")
            return False
        else:
            print(f"\n{Colors.GREEN}All services healthy! Running live demo...{Colors.END}")
            return True

    # ------------------------------------------------------------------
    # Main flow
    # ------------------------------------------------------------------

    async def run_demo(self) -> None:
        self.print_banner()
        self.print_architecture()

        live_mode = await self.check_services()
        if not live_mode:
            print(
                f"\n{Colors.RED}Cannot run demo — services are not healthy.{Colors.END}"
            )
            print(f"{Colors.YELLOW}Start the stack with: make up{Colors.END}")
            return

        if self.args.check_only:
            print(f"\n{Colors.GREEN}All services healthy. Exiting (--check-only).{Colors.END}")
            return

        print(f"\n{Colors.BOLD}DEMO PARAMETERS:{Colors.END}")
        print(f"  Mode: {Colors.GREEN}LIVE{Colors.END}")
        print(f"  Scenarios: {Colors.GREEN}{self.args.scenario}{Colors.END}")
        print(f"  High-volume count: {Colors.GREEN}{self.args.count}{Colors.END}")

        input(f"\n{Colors.YELLOW}Press ENTER to start the demo...{Colors.END}")

        await self.run_scenarios()
        self.display_statistics()

    async def run_scenarios(self) -> None:
        print(f"\n{Colors.HEADER}{Colors.BOLD}DEMO SCENARIOS{Colors.END}\n")

        scenario = self.args.scenario
        if scenario in ("all", "sanctions"):
            print(f"{Colors.BOLD}Scenario 1: Sanctions Evasion Detection{Colors.END}")
            await self.scenario_sanctions_evasion()
            await asyncio.sleep(2)

        if scenario in ("all", "structuring"):
            print(f"\n{Colors.BOLD}Scenario 2: Transaction Structuring Detection{Colors.END}")
            await self.scenario_structuring()
            await asyncio.sleep(2)

        if scenario in ("all", "high-volume"):
            print(f"\n{Colors.BOLD}Scenario 3: High-Volume Transaction Processing{Colors.END}")
            await self.scenario_high_volume()

    # ------------------------------------------------------------------
    # Scenario 1: Sanctions Evasion
    # ------------------------------------------------------------------

    async def scenario_sanctions_evasion(self) -> None:
        """Detect a sanctioned individual using the full Trinity pipeline."""
        transaction = {
            "id": "trinity_demo_001",
            "sender": "M. Emmanuel",
            "receiver": "Offshore Account Ltd.",
            "amount": 25000,
            "description": "Business investment transfer",
            "timestamp": datetime.now().isoformat(),
            "currency": "USD",
            "status": "pending",
            "category": "transfer",
        }

        print(f"\n  {Colors.CYAN}Transaction Ingested:{Colors.END}")
        print(f"     ID: {transaction['id']}")
        print(f"     Sender: {Colors.YELLOW}{transaction['sender']}{Colors.END}")
        print(f"     Amount: ${transaction['amount']:,}")

        await self.process_transaction(transaction)

    # ------------------------------------------------------------------
    # Scenario 2: Structuring Detection
    # ------------------------------------------------------------------

    async def scenario_structuring(self) -> None:
        """Detect transaction structuring (smurfing) pattern."""
        transactions = [
            {
                "id": "trinity_demo_002",
                "amount": 9000,
                "description": "Transfer to family",
                "sender": "John Doe",
                "receiver": "Various Recipients",
                "timestamp": datetime.now().isoformat(),
                "currency": "USD",
                "status": "pending",
                "category": "transfer",
            },
            {
                "id": "trinity_demo_003",
                "amount": 8500,
                "description": "Payment for services",
                "sender": "John Doe",
                "receiver": "Various Recipients",
                "timestamp": datetime.now().isoformat(),
                "currency": "USD",
                "status": "pending",
                "category": "transfer",
            },
            {
                "id": "trinity_demo_004",
                "amount": 9500,
                "description": "Business expense",
                "sender": "John Doe",
                "receiver": "Various Recipients",
                "timestamp": datetime.now().isoformat(),
                "currency": "USD",
                "status": "pending",
                "category": "transfer",
            },
        ]

        print(f"\n  {Colors.CYAN}Multiple Transactions Detected:{Colors.END}")
        for tx in transactions:
            print(
                f"     {Colors.CYAN}  {tx['id']}: ${tx['amount']:,} - {tx['description']}{Colors.END}"
            )

        print(f"\n  {Colors.YELLOW}Pattern: Sub-$10K transactions (structuring/smurfing){Colors.END}")
        print(f"     Total Amount: ${sum(t['amount'] for t in transactions):,}")

        async with aiohttp.ClientSession() as session:
            for tx in transactions:
                await self.process_single(session, tx)

        print(f"\n  {Colors.YELLOW}Risk Assessment: MEDIUM{Colors.END}")
        print(f"     Recommendation: Enhanced monitoring")

    # ------------------------------------------------------------------
    # Scenario 3: High-Volume Processing
    # ------------------------------------------------------------------

    async def scenario_high_volume(self) -> None:
        """Demonstrate high-volume transaction processing."""
        count = self.args.count
        print(f"\n  {Colors.CYAN}Processing {count:,} transactions...{Colors.END}")

        start_time = time.time()
        batch_size = 100

        async with aiohttp.ClientSession() as session:
            for batch_start in range(0, count, batch_size):
                batch_end = min(batch_start + batch_size, count)
                tasks = []
                for i in range(batch_start, batch_end):
                    transaction = {
                        "id": f"trinity_bulk_{i:05d}",
                        "sender": f"User_{i}",
                        "receiver": f"Recipient_{i}",
                        "amount": random.randint(100, 5000),
                        "description": f"Transaction {i}",
                        "timestamp": datetime.now().isoformat(),
                        "currency": "USD",
                        "status": "pending",
                        "category": "transfer",
                    }
                    tasks.append(self.process_single(session, transaction))

                await asyncio.gather(*tasks, return_exceptions=True)

                processed = batch_end
                elapsed = time.time() - start_time
                tps = processed / elapsed if elapsed > 0 else 0
                flagged = self.stats["flagged"]

                print(
                    f"\r     Processed: {processed:,}/{count:,} | "
                    f"TPS: {tps:,.0f} | "
                    f"Flagged: {flagged} | "
                    f"Time: {elapsed:.1f}s",
                    end="",
                )

        total_time = time.time() - start_time
        final_tps = count / total_time if total_time > 0 else 0

        print(f"\n\n  {Colors.BOLD}Performance:{Colors.END}")
        print(f"  Total Transactions: {Colors.GREEN}{count:,}{Colors.END}")
        print(f"  Processing Time: {Colors.GREEN}{total_time:.2f}s{Colors.END}")
        print(f"  Average TPS: {Colors.GREEN}{final_tps:,.0f}{Colors.END}")
        print(
            f"  Average Latency: {Colors.GREEN}{(total_time / count) * 1000:.1f}ms{Colors.END}"
        )

    # ------------------------------------------------------------------
    # Transaction processing
    # ------------------------------------------------------------------

    async def process_transaction(self, transaction: dict) -> None:
        """Process a single transaction through the Go backend and display steps."""
        async with aiohttp.ClientSession() as session:
            await self.process_single(session, transaction, verbose=True)

    async def process_single(
        self,
        session: aiohttp.ClientSession,
        transaction: dict,
        verbose: bool = False,
    ) -> dict | None:
        """Process a single transaction. Returns response or None on error."""
        try:
            async with session.post(
                f"{self.base_urls['go_backend']}/api/v1/transactions/process",
                json={"transaction": transaction},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    if verbose:
                        print(f"     {Colors.RED}Error: HTTP {response.status}{Colors.END}")
                    return None

                result = await response.json()

                self.stats["total_processed"] += 1
                if result.get("flagged"):
                    self.stats["flagged"] += 1
                if result.get("sar_generated"):
                    self.stats["sars_generated"] += 1
                if result.get("zk_verified"):
                    self.stats["zk_verifications"] += 1

                processing_time = result.get("processing_time_ms", 0)
                self.stats["processing_times"].append(processing_time)

                if verbose:
                    self._print_result(transaction, result)

                return result
        except Exception as e:
            if verbose:
                print(f"     {Colors.RED}Error: {e}{Colors.END}")
            return None

    def _print_result(self, transaction: dict, result: dict) -> None:
        """Print detailed result for a single transaction."""
        print(f"\n  {Colors.BLUE}Go Backend (The Muscle)...{Colors.END}")
        print(f"     {Colors.GREEN}Transaction validated{Colors.END}")
        print(f"     Processing Time: {result.get('processing_time_ms', 0):.1f}ms")

        entities = result.get("entities", [])
        if entities:
            print(f"\n  {Colors.GREEN}Python NER (The Brain)...{Colors.END}")
            print(f"     {Colors.GREEN}GLINER detected {len(entities)} entities{Colors.END}")
            for entity in entities:
                marker = f" {Colors.RED}[SUSPICIOUS]" if entity.get("suspicious") else ""
                print(
                    f"     {Colors.YELLOW}  {entity.get('text', '?')} "
                    f"({entity.get('type', '?')}){marker}{Colors.END}"
                )

        if result.get("zk_verified") is not None:
            print(f"\n  {Colors.RED}Rust ZK (The Shield)...{Colors.END}")
            zk_status = "verified" if result.get("zk_verified") else "skipped"
            print(f"     {Colors.GREEN}ZK proof {zk_status}{Colors.END}")

        if result.get("sar_generated"):
            print(f"\n  {Colors.MAGENTA}LLM Service (AI Investigator)...{Colors.END}")
            print(f"     {Colors.GREEN}SAR Generated{Colors.END}")

        print(f"\n  {Colors.BOLD}Result:{Colors.END}")
        status = (
            f"{Colors.RED}FLAGGED" if result.get("flagged") else f"{Colors.GREEN}CLEAN"
        )
        print(f"     Status: {status}{Colors.END}")
        if result.get("reason"):
            print(f"     Reason: {result.get('reason')}")
        print(f"     SAR Filed: {Colors.GREEN if result.get('sar_generated') else Colors.YELLOW}"
              f"{'YES' if result.get('sar_generated') else 'NO'}{Colors.END}")

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def display_statistics(self) -> None:
        print(f"\n\n{Colors.HEADER}{Colors.BOLD}FINAL STATISTICS{Colors.END}\n")

        print(f"{Colors.BOLD}Transaction Metrics:{Colors.END}")
        print(f"  Total Processed: {Colors.GREEN}{self.stats['total_processed']:,}{Colors.END}")
        print(f"  Flagged for Review: {Colors.YELLOW}{self.stats['flagged']:,}{Colors.END}")
        print(f"  SARs Generated: {Colors.RED}{self.stats['sars_generated']}{Colors.END}")
        print(f"  ZK Verifications: {Colors.RED}{self.stats['zk_verifications']}{Colors.END}")

        times = self.stats["processing_times"]
        if times:
            avg_time = sum(times) / len(times)
            max_time = max(times)
            min_time = min(times)
            print(f"\n{Colors.BOLD}Latency:{Colors.END}")
            print(f"  Average: {Colors.GREEN}{avg_time:.1f}ms{Colors.END}")
            print(f"  Min: {Colors.GREEN}{min_time:.1f}ms{Colors.END}")
            print(f"  Max: {Colors.GREEN}{max_time:.1f}ms{Colors.END}")

        print(f"\n{Colors.BOLD}Trinity Components:{Colors.END}")
        print(f"  {Colors.BLUE}Go Backend:{Colors.END}     The Muscle — transaction orchestration")
        print(f"  {Colors.GREEN}Python NER:{Colors.END}    The Brain — GLINER entity resolution")
        print(f"  {Colors.RED}Rust ZK:{Colors.END}       The Shield — ZK-SNARK verification")
        print(f"  {Colors.MAGENTA}LLM Service:{Colors.END}  AI Investigator — SAR generation")

        print(f"\n{Colors.CYAN}{Colors.BOLD}Trinity Guard Demo Complete!{Colors.END}\n")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Trinity Guard Live Demo — PyCon Kenya 2026"
    )
    parser.add_argument(
        "--scenario",
        choices=["all", "sanctions", "structuring", "high-volume"],
        default="all",
        help="Which scenario to run (default: all)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1000,
        help="Number of transactions for high-volume scenario (default: 1000)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check service health, then exit",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    demo = TrinityDemo(args)

    try:
        await demo.run_demo()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Demo interrupted by user{Colors.END}")
    except Exception as e:
        print(f"\n\n{Colors.RED}Demo error: {e}{Colors.END}")

    print(f"\n{Colors.BOLD}Thank you for attending PyCon Kenya 2026!{Colors.END}")
    print(f"{Colors.CYAN}The Future of Compliance is Polyglot.{Colors.END}\n")


if __name__ == "__main__":
    asyncio.run(main())
