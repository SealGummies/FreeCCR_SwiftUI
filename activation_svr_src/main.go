package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
)

// LemonSqueezy API configuration
const (
	LemonSqueezyAPIURL = "https://api.lemonsqueezy.com"
	StoreID           = 189887
	PersonalVariantID = 848987
	Personal1YearVariantID = 869418
	CommercialVariantID = 848972
	PersonalVariantIDLive = 873882
	Personal1YearVariantIDLive = 873883
	CommercialVariantIDLive = 873885
)

// Request/Response structures
type ValidateRequest struct {
	LicenseKey string `json:"license_key" binding:"required"`
	InstanceID string `json:"instance_id" binding:"required"`
}

type ValidateResponse struct {
	Valid bool `json:"valid"`
}

type ActivateRequest struct {
	LicenseKey   string `json:"license_key" binding:"required"`
	InstanceName string `json:"instance_name" binding:"required"`
	Email        string `json:"email" binding:"required"`
}

type ActivateResponse struct {
	Activated   bool      `json:"activated"`
	Instance    Instance  `json:"instance"`
	Meta        Meta      `json:"meta"`
	LicenseType string    `json:"license_type"`
}

type DeactivateRequest struct {
	LicenseKey string `json:"license_key" binding:"required"`
	InstanceID string `json:"instance_id" binding:"required"`
}

type DeactivateResponse struct {
	Deactivated bool `json:"deactivated"`
}

type Instance struct {
	ID string `json:"id"`
}

type Meta struct {
	StoreID        int    `json:"store_id"`
	VariantID      int    `json:"variant_id"`
	CustomerEmail  string `json:"customer_email"`
}

// LicenseProvider interface for abstraction
type LicenseProvider interface {
	ValidateLicense(licenseKey, instanceID string) (*ValidateResponse, error)
	ActivateLicense(licenseKey, instanceName string) (*ActivateResponse, error)
	DeactivateLicense(licenseKey, instanceID string) (*DeactivateResponse, error)
}

// LemonSqueezyProvider implements LicenseProvider
type LemonSqueezyProvider struct {
	APIKey string
	Client *http.Client
}

func NewLemonSqueezyProvider(apiKey string) *LemonSqueezyProvider {
	return &LemonSqueezyProvider{
		APIKey: apiKey,
		Client: &http.Client{Timeout: 10 * time.Second},
	}
}

func (l *LemonSqueezyProvider) ValidateLicense(licenseKey, instanceID string) (*ValidateResponse, error) {
	url := fmt.Sprintf("%s/v1/licenses/validate", LemonSqueezyAPIURL)
	
	data := map[string]string{
		"license_key": licenseKey,
		"instance_id": instanceID,
	}
	
	resp, err := l.makeRequest("POST", url, data)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	
	if resp.StatusCode >= 500 {
		// Server error: treat as valid for offline use
		return &ValidateResponse{Valid: true}, nil
	}
	
	var result ValidateResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}
	
	return &result, nil
}

func (l *LemonSqueezyProvider) ActivateLicense(licenseKey, instanceName string) (*ActivateResponse, error) {
	url := fmt.Sprintf("%s/v1/licenses/activate", LemonSqueezyAPIURL)
	
	data := map[string]string{
		"license_key":   licenseKey,
		"instance_name": instanceName,
	}
	
	resp, err := l.makeRequest("POST", url, data)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("activation failed with status: %d", resp.StatusCode)
	}
	
	var result ActivateResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}
	
	return &result, nil
}

func (l *LemonSqueezyProvider) DeactivateLicense(licenseKey, instanceID string) (*DeactivateResponse, error) {
	url := fmt.Sprintf("%s/v1/licenses/deactivate", LemonSqueezyAPIURL)
	
	data := map[string]string{
		"license_key": licenseKey,
		"instance_id": instanceID,
	}
	
	resp, err := l.makeRequest("POST", url, data)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("deactivation failed with status: %d", resp.StatusCode)
	}
	
	var result DeactivateResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}
	
	return &result, nil
}

func (l *LemonSqueezyProvider) makeRequest(method, url string, data map[string]string) (*http.Response, error) {
	jsonData, err := json.Marshal(data)
	if err != nil {
		return nil, err
	}
	
	req, err := http.NewRequest(method, url, bytes.NewBuffer(jsonData))
	if err != nil {
		return nil, err
	}
	
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")
	if l.APIKey != "" {
		req.Header.Set("Authorization", "Bearer "+l.APIKey)
	}
	
	return l.Client.Do(req)
}

// LicenseService handles license operations
type LicenseService struct {
	Provider LicenseProvider
}

func NewLicenseService(provider LicenseProvider) *LicenseService {
	return &LicenseService{Provider: provider}
}

func (s *LicenseService) ValidateHandler(c *gin.Context) {
	var req ValidateRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	
	result, err := s.Provider.ValidateLicense(req.LicenseKey, req.InstanceID)
	if err != nil {
		log.Printf("Validation error: %v", err)
		// On error, allow offline use
		c.JSON(http.StatusOK, ValidateResponse{Valid: true})
		return
	}
	
	c.JSON(http.StatusOK, result)
}

func (s *LicenseService) ActivateHandler(c *gin.Context) {
	var req ActivateRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	
	result, err := s.Provider.ActivateLicense(req.LicenseKey, req.InstanceName)
	if err != nil {
		log.Printf("Activation error: %v", err)
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	
	// Validate email matches
	customerEmail := result.Meta.CustomerEmail
	if customerEmail != "" && strings.ToLower(strings.TrimSpace(customerEmail)) != strings.ToLower(strings.TrimSpace(req.Email)) {
		// Email validation failed - deactivate the license that was just activated
		log.Printf("Email validation failed for license %s: expected %s, got %s", req.LicenseKey, customerEmail, req.Email)
		if _, deactivateErr := s.Provider.DeactivateLicense(req.LicenseKey, result.Instance.ID); deactivateErr != nil {
			log.Printf("Failed to deactivate license after email validation failure: %v", deactivateErr)
		}
		c.JSON(http.StatusBadRequest, gin.H{"error": "Email does not match license"})
		return
	}
	
	// Validate store ID
	if result.Meta.StoreID != StoreID {
		// Store ID validation failed - deactivate the license that was just activated
		log.Printf("Store ID validation failed for license %s: expected %d, got %d", req.LicenseKey, StoreID, result.Meta.StoreID)
		if _, deactivateErr := s.Provider.DeactivateLicense(req.LicenseKey, result.Instance.ID); deactivateErr != nil {
			log.Printf("Failed to deactivate license after store ID validation failure: %v", deactivateErr)
		}
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid store ID"})
		return
	}
	
	// Determine license type based on variant ID
	var licenseType string
	switch result.Meta.VariantID {
	case PersonalVariantID, Personal1YearVariantID, PersonalVariantIDLive, Personal1YearVariantIDLive:
		licenseType = "Personal"
	case CommercialVariantID, CommercialVariantIDLive:
		licenseType = "Commercial"
	default:
		// Variant ID validation failed - deactivate the license that was just activated
		log.Printf("Variant ID validation failed for license %s: unexpected variant ID %d", req.LicenseKey, result.Meta.VariantID)
		if _, deactivateErr := s.Provider.DeactivateLicense(req.LicenseKey, result.Instance.ID); deactivateErr != nil {
			log.Printf("Failed to deactivate license after variant ID validation failure: %v", deactivateErr)
		}
		c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid variant ID"})
		return
	}
	
	// Add license type to response
	result.LicenseType = licenseType
	
	c.JSON(http.StatusOK, result)
}

func (s *LicenseService) DeactivateHandler(c *gin.Context) {
	var req DeactivateRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	
	result, err := s.Provider.DeactivateLicense(req.LicenseKey, req.InstanceID)
	if err != nil {
		log.Printf("Deactivation error: %v", err)
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	
	c.JSON(http.StatusOK, result)
}

func main() {
	// Load configuration
	config := LoadConfig()
	
	// Initialize the license provider using factory
	provider := ProviderFactory(config)
	
	// Initialize the license service
	licenseService := NewLicenseService(provider)
	
	// Setup Gin router
	r := gin.Default()
	
	// Add CORS middleware
	r.Use(func(c *gin.Context) {
		c.Header("Access-Control-Allow-Origin", "*")
		c.Header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
		c.Header("Access-Control-Allow-Headers", "Content-Type, Authorization")
		
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(204)
			return
		}
		
		c.Next()
	})
	
	// Health check endpoint
	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "healthy"})
	})
	
	// License management endpoints
	api := r.Group("/v1")
	{
		licenses := api.Group("/licenses")
		{
			licenses.POST("/validate", licenseService.ValidateHandler)
			licenses.POST("/activate", licenseService.ActivateHandler)
			licenses.POST("/deactivate", licenseService.DeactivateHandler)
		}
	}
	
	// Get port from environment or default to 8080
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	
	log.Printf("Server starting on port %s", port)
	if err := r.Run(":" + port); err != nil {
		log.Fatal("Failed to start server:", err)
	}
}