import unittest
import sys
import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock

def run_all_tests():
    """Discovers and runs all tests in the tests/ directory."""
    print("="*60)
    print("🔍 PDF App Test Suite Runner")
    print("="*60)
    
    # Discover all tests in the "tests" directory
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir='tests', pattern='test_*.py')
    
    # Run the tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        print("\n✅ All tests passed successfully!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed. Please review the output above.")
        sys.exit(1)

def run_stress_tests(iterations=1000, concurrency=20):
    """Simulates heavy usage by hitting the manager with rapid concurrent requests."""
    print("="*60)
    print(f"🔥 Running PDF App Stress Tests (Iterations: {iterations}, Threads: {concurrency})")
    print("="*60)
    
    # Dynamically import inside to ensure it doesn't break standard discovery
    from core.llm_manager import LocalLLMManager
    
    start_time = time.time()
    success_count = 0
    failure_count = 0

    # Mock all external heavy processes so we only test the Python logic handling the load
    with patch('core.llm_manager.requests.post') as mock_post, \
         patch('core.llm_manager.requests.get') as mock_get, \
         patch('core.llm_manager.subprocess.Popen') as mock_popen:
        
        # Keep server check happy
        mock_get.return_value.status_code = 200
        
        # Setup a fake streaming response for the queries
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_lines.return_value = [
            b'{"response": "Mocked "}', 
            b'{"response": "stress "}', 
            b'{"response": "response."}'
        ]
        mock_post.return_value.__enter__.return_value = mock_response
        
        llm_manager = LocalLLMManager()
        
        def simulate_user_action(idx):
            try:
                chunks = []
                llm_manager.query(
                    question=f"Simulated user rapid query {idx}", 
                    selected_model="llama3", 
                    allowed_docs=[], 
                    callback=lambda x: chunks.append(x), 
                    rag_enabled=False, 
                    use_agents=False
                )
                # Verify that the mocked response was processed correctly
                if "".join(chunks) == "Mocked stress response.":
                    return True
                return False
            except Exception as e:
                return False

        # Hammer the application with concurrent simulated users
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(executor.map(simulate_user_action, range(iterations)))
            
        success_count = sum(results)
        failure_count = len(results) - success_count
        
    elapsed = time.time() - start_time
    print(f"\n⏱️ Stress Test Completed in {elapsed:.2f} seconds.")
    print(f"✅ Successful Mocked Operations: {success_count}")
    print(f"❌ Failed Operations: {failure_count}")
    
    if failure_count == 0:
        print("\n🚀 App handled the heavy simulated load perfectly!")
        sys.exit(0)
    else:
        print("\n⚠️ App struggled under the simulated load.")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PDF App Test Runner")
    parser.add_argument('--stress', action='store_true', help='Run stress tests instead of unit tests')
    parser.add_argument('--iterations', type=int, default=1000, help='Number of iterations for the stress test')
    parser.add_argument('--concurrency', type=int, default=20, help='Number of concurrent threads to simulate')
    
    args = parser.parse_args()
    
    if args.stress:
        run_stress_tests(args.iterations, args.concurrency)
    else:
        run_all_tests()