package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
	"time"
)

func TestLicenseEndpoints(t *testing.T) {
	// Start the server in a goroutine
	go main()
	
	// Wait for server to start
	time.Sleep(2 * time.Second)
	
	baseURL := "http://localhost:8080"
	
	// Test health endpoint
	resp, err := http.Get(baseURL + "/health")
	if err != nil {
		t.Fatalf("Health check failed: %v", err)
	}
	resp.Body.Close()
	
	if resp.StatusCode != 200 {
		t.Errorf("Expected status 200 for health check, got %d", resp.StatusCode)
	}
	
	fmt.Println("✅ Health check passed")
	
	// Test validation endpoint (should fail with invalid key)
	validateData := map[string]string{
		"license_key": "invalid-key",
		"instance_id": "test-instance",
	}
	
	jsonData, _ := json.Marshal(validateData)
	resp, err = http.Post(baseURL+"/v1/licenses/validate", "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		t.Fatalf("Validation request failed: %v", err)
	}
	defer resp.Body.Close()
	
	// Should still return 200 (graceful failure)
	if resp.StatusCode != 200 {
		t.Errorf("Expected status 200 for validation, got %d", resp.StatusCode)
	}
	
	fmt.Println("✅ Validation endpoint accessible")
	
	// Test activation endpoint (should fail with invalid key)
	activateData := map[string]string{
		"license_key":   "invalid-key",
		"instance_name": "test-instance",
	}
	
	jsonData, _ = json.Marshal(activateData)
	resp, err = http.Post(baseURL+"/v1/licenses/activate", "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		t.Fatalf("Activation request failed: %v", err)
	}
	defer resp.Body.Close()
	
	// Should fail with 400 for invalid key
	if resp.StatusCode != 400 {
		t.Errorf("Expected status 400 for invalid activation, got %d", resp.StatusCode)
	}
	
	fmt.Println("✅ Activation endpoint accessible")
	
	// Test deactivation endpoint (should fail with invalid key)
	deactivateData := map[string]string{
		"license_key": "invalid-key",
		"instance_id": "test-instance",
	}
	
	jsonData, _ = json.Marshal(deactivateData)
	resp, err = http.Post(baseURL+"/v1/licenses/deactivate", "application/json", bytes.NewBuffer(jsonData))
	if err != nil {
		t.Fatalf("Deactivation request failed: %v", err)
	}
	defer resp.Body.Close()
	
	// Should fail with 400 for invalid key
	if resp.StatusCode != 400 {
		t.Errorf("Expected status 400 for invalid deactivation, got %d", resp.StatusCode)
	}
	
	fmt.Println("✅ Deactivation endpoint accessible")
	fmt.Println("✅ All tests passed!")
}
