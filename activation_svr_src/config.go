package main

import (
	"os"
)

// Config holds the application configuration
type Config struct {
	Provider     string
	LemonSqueezy LemonSqueezyConfig
	// Add other provider configs here as needed
	// OtherProvider OtherProviderConfig
}

type LemonSqueezyConfig struct {
	APIKey   string
	StoreID  int
	Variants map[string][]int
}

// LoadConfig loads configuration from environment variables
func LoadConfig() *Config {
	return &Config{
		Provider: getEnvWithDefault("LICENSE_PROVIDER", "lemonsqueezy"),
		LemonSqueezy: LemonSqueezyConfig{
			APIKey:  os.Getenv("LEMON_SQUEEZY_API_KEY"),
			StoreID: 189887, // Could be made configurable
			Variants: map[string][]int{
				"personal":        {848987, 873882},
				"personal_1_year": {869418, 873883},
				"commercial":      {848972, 873885},
			},
		},
	}
}

func getEnvWithDefault(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}

// ProviderFactory creates the appropriate license provider based on configuration
func ProviderFactory(config *Config) LicenseProvider {
	switch config.Provider {
	case "lemonsqueezy":
		return NewLemonSqueezyProvider(config.LemonSqueezy.APIKey)
	// Add cases for other providers here:
	// case "otherprovider":
	//     return NewOtherProvider(config.OtherProvider)
	default:
		// Default to Lemon Squeezy
		return NewLemonSqueezyProvider(config.LemonSqueezy.APIKey)
	}
}
